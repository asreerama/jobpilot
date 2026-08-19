#!/usr/bin/env python
"""Resolve LinkedIn-sourced jobs to the employer's own ATS posting.

Firecrawl refuses linkedin.com itself, but its /search endpoint finds the
direct Greenhouse/Lever/Ashby/etc posting for a company+title in one call.
Rewriting the row (apply_url + ats) turns a LinkedIn job into a first-class
ATS job the fleet applies to directly, with no web-searching inside the
apply session and no LinkedIn login anywhere.

    bin/resolve_linkedin.py --limit 40 [--status queued,needs_jd,discovered]

Key: FIRECRAWL_API_KEY from env or ~/.config/jobpilot/firecrawl.env.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobpilot import db                       # noqa: E402
from jobpilot.config import load              # noqa: E402
from jobpilot.normalize import canonical_url  # noqa: E402

API = "https://api.firecrawl.dev/v2/search"

# domain -> ats label the pipeline understands
ATS_DOMAINS = {
    "job-boards.greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "apply.workable.com": "workable",
    "jobs.smartrecruiters.com": "smartrecruiters",
    "myworkdayjobs.com": "workday",
    "recruitee.com": "recruitee",
}


def api_key():
    k = os.environ.get("FIRECRAWL_API_KEY")
    if k:
        return k
    try:
        with open(os.path.expanduser("~/.config/jobpilot/firecrawl.env")) as f:
            for line in f:
                if line.startswith("FIRECRAWL_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    sys.exit("no FIRECRAWL_API_KEY")


def search(key, query):
    req = urllib.request.Request(
        API, data=json.dumps({"query": query, "limit": 6}).encode(),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("data", {}).get("web", []) or []


def title_matches(want, got):
    """Loose match: most significant words of the wanted title appear."""
    stop = {"senior", "sr", "sr.", "staff", "lead", "principal", "ii", "iii",
            "i", "the", "a", "an", "of", "and", "&", "-", "remote"}
    w = [x for x in re.findall(r"[a-z0-9]+", (want or "").lower())
         if x not in stop]
    g = (got or "").lower()
    if not w:
        return False
    hits = sum(1 for x in w if x in g)
    return hits >= max(2, int(len(w) * 0.6))


def company_matches(company, url, result_title):
    """Guard against title-only false matches (Jerry -> otter greenhouse
    board on 2026-08-19). The board slug in the URL, or the result text,
    must share a significant token with the company name."""
    co = re.findall(r"[a-z0-9]+", (company or "").lower())
    stop = {"inc", "llc", "the", "ai", "io", "labs", "technologies", "co",
            "corp", "group", "and", "of"}
    co = [w for w in co if w not in stop and len(w) > 2]
    if not co:
        return True                    # nothing to check against; allow
    hay = (url + " " + (result_title or "")).lower()
    # board slug is the path segment after the ATS domain
    return any(w in hay for w in co)


def _scan(job, results):
    for r in results:
        url = r.get("url") or ""
        host = re.sub(r"^https?://", "", url).split("/")[0]
        for dom, ats in ATS_DOMAINS.items():
            if host.endswith(dom) or dom in host:
                if url.rstrip("/").endswith(dom):
                    continue          # board landing page, not a posting
                if len(url.rstrip("/").split("/")) < 5:
                    continue
                if (title_matches(job["title"], r.get("title", ""))
                        and company_matches(job["company"], url,
                                            r.get("title", ""))):
                    return {"url": url, "ats": ats}
    return None


def resolve_one(key, job):
    co = job["company"] or ""
    ti = job["title"] or ""
    # Three query forms, cheapest-first; stop at the first ATS hit. Company
    # sites vary in how they surface on search, so more than one phrasing
    # materially lifts the hit rate.
    queries = [
        'site:job-boards.greenhouse.io OR site:jobs.lever.co OR '
        'site:jobs.ashbyhq.com OR site:jobs.smartrecruiters.com "%s" %s' % (ti, co),
        '%s "%s" apply job greenhouse OR lever OR ashby OR workday' % (co, ti),
        '%s careers "%s"' % (co, ti),
    ]
    err = "no_direct_posting"
    for q in queries:
        try:
            hit = _scan(job, search(key, q))
        except Exception as e:
            err = "search_error:%s" % type(e).__name__
            continue
        if hit:
            return hit, None
    return None, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--status", default="queued,discovered,needs_jd")
    args = ap.parse_args()
    key = api_key()
    cfg = load()
    conn = db.connect(cfg["_db_path"])
    statuses = [s for s in args.status.split(",") if s]
    rows = conn.execute(
        "SELECT * FROM jobs WHERE ats='linkedin' AND status IN (%s)"
        " ORDER BY COALESCE(score,0) DESC LIMIT ?"
        % ",".join("?" * len(statuses)),
        (*statuses, args.limit)).fetchall()
    print("resolving %d linkedin jobs" % len(rows), flush=True)
    hit = miss = 0
    for j in rows:
        found, err = resolve_one(key, j)
        if found:
            cu = canonical_url(found["url"])
            dup = conn.execute(
                "SELECT id FROM jobs WHERE canonical_url=? AND id!=?",
                (cu, j["id"])).fetchone()
            if dup:
                db.set_status(conn, j["id"], "duplicate",
                              filter_reason="resolved to already-known %s"
                              % found["ats"])
            else:
                conn.execute(
                    "UPDATE jobs SET apply_url=?, ats=?, canonical_url=?,"
                    " updated_at=? WHERE id=? AND status NOT IN ('applied',"
                    "'applying')",
                    (found["url"], found["ats"], cu, db.utcnow(), j["id"]))
                hit += 1
                print("  %s | %s -> %s" % (j["company"], j["title"][:40],
                                           found["url"]), flush=True)
        else:
            miss += 1
        conn.commit()
        time.sleep(1)     # be polite to the search API
    print("done: %d resolved, %d without a direct posting" % (hit, miss))


if __name__ == "__main__":
    main()

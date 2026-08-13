"""Seed the companies table: verified board lists + your application history.

Sources:
  seeds/all-verified.json   wickfeed/ats-job-aggregator (live-verified boards)
  seeds/gh_companies.json   Feashliaa slug arrays (contain junk; probed first)
  seeds/ashby_companies.json, seeds/lever_companies.json
  applications.csv          every company already applied to (incl. Workday hosts)
"""
import csv
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from . import db
from .config import load
from .normalize import detect_ats

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

PROBES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}",
    "lever": "https://api.lever.co/v0/postings/{}?mode=json&limit=1",
}


def probe(ats, slug):
    try:
        r = requests.get(PROBES[ats].format(slug), headers=UA, timeout=8)
        return (ats, slug, r.status_code == 200)
    except requests.RequestException:
        return (ats, slug, False)


def seed_verified(conn, root):
    path = os.path.join(root, "seeds", "all-verified.json")
    added = 0
    with open(path) as f:
        for e in json.load(f):
            ats = e.get("ats")
            if ats not in ("greenhouse", "lever", "ashby", "workable",
                           "recruitee", "smartrecruiters"):
                continue
            if db.add_company(conn, e["token"].lower(), ats,
                              e.get("company") or e["token"],
                              source="wickfeed-verified"):
                added += 1
    conn.commit()
    return added


def seed_probed(conn, root, max_probe=6000):
    """Feashliaa slug arrays are noisy: probe before inserting."""
    candidates = []
    for fn, ats in (("gh_companies.json", "greenhouse"),
                    ("ashby_companies.json", "ashby"),
                    ("lever_companies.json", "lever")):
        path = os.path.join(root, "seeds", fn)
        if not os.path.exists(path):
            continue
        with open(path) as f:
            slugs = json.load(f)
        existing = {r["slug"] for r in conn.execute(
            "SELECT slug FROM companies WHERE ats=?", (ats,))}
        for s in slugs:
            s = str(s).strip().lower()
            if s and s not in existing and re.fullmatch(r"[a-z0-9._%-]{2,60}", s):
                candidates.append((ats, s))
    candidates = candidates[:max_probe]
    added = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(probe, ats, slug) for ats, slug in candidates]
        for i, fut in enumerate(as_completed(futs)):
            ats, slug, ok = fut.result()
            if ok and db.add_company(conn, slug, ats, slug,
                                     source="feashliaa-probed"):
                added += 1
            if i and i % 500 == 0:
                conn.commit()
                print(f"  probed {i}/{len(candidates)}, added {added}",
                      flush=True)
    conn.commit()
    return added, len(candidates)


WD_RE = re.compile(
    r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:([a-zA-Z]{2}(?:-[a-zA-Z]{2})?)/)?([^/]+)/",
    re.I)
LOCALE_RE = re.compile(r"^[a-z]{2}(-[a-zA-Z]{2})?$")


def seed_history(conn, csv_path):
    added = 0
    try:
        f = open(csv_path, newline="")
    except FileNotFoundError:
        return 0
    with f:
        for row in csv.DictReader(f):
            url = (row.get("url") or "").strip()
            if not url:
                continue
            m = WD_RE.match(url)
            if m:
                tenant, wd, _loc, site = m.groups()
                if LOCALE_RE.match(site):
                    continue  # url shape we can't parse a site from
                meta = json.dumps(
                    {"host": f"{tenant}.{wd}.myworkdayjobs.com", "site": site})
                if db.add_company(conn, tenant.lower(), "workday",
                                  row.get("company") or tenant,
                                  source="history", meta=meta):
                    added += 1
                continue
            ats, slug = detect_ats(url)
            if slug and ats in ("greenhouse", "lever", "ashby", "workable",
                                "recruitee", "smartrecruiters"):
                if db.add_company(conn, slug, ats,
                                  row.get("company") or slug,
                                  source="history"):
                    added += 1
    conn.commit()
    return added


def main():
    cfg = load()
    conn = db.connect(cfg["_db_path"])
    root = cfg["_root"]
    quick = "--quick" in sys.argv
    print("seeding from wickfeed verified list...")
    print(f"  added {seed_verified(conn, root)}")
    print("seeding from application history...")
    print(f"  added {seed_history(conn, cfg['_csv_path'])}")
    if not quick:
        print("probing Feashliaa slug lists (one-time, a few minutes)...")
        added, probed = seed_probed(conn, root)
        print(f"  added {added} of {probed} probed")
    total = conn.execute(
        "SELECT COUNT(*) c FROM companies WHERE active=1").fetchone()["c"]
    print(f"companies active: {total}")


if __name__ == "__main__":
    main()

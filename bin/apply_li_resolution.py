#!/usr/bin/env python
"""Apply LinkedIn voyager-API resolution results to the DB.

The logged-in browser fetches LinkedIn's own applyMethod for each job and
writes a JSON array of {jid, offsite, easy} to a file. This reads that file
and rewrites each matching row:

  - offsite URL on a known ATS -> ats=<that ats>, apply_url=<url>, status back
    to 'discovered' so match/score/queue picks it up as a first-class ATS job
  - offsite URL on an unknown host -> ats='other', still applyable by the
    generic flow
  - easy-apply-only -> status 'needs_human', note 'linkedin easy apply only',
    so it surfaces for a throttled manual pass instead of silently rotting
  - no apply method -> left as-is

Never touches applied/applying rows. Ground truth from LinkedIn itself, so no
company/title false-match risk (unlike the Firecrawl search resolver).

    bin/apply_li_resolution.py <results.json>
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobpilot import db                       # noqa: E402
from jobpilot.config import load              # noqa: E402
from jobpilot.normalize import canonical_url  # noqa: E402

ATS_HOSTS = {
    "job-boards.greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "ashbyhq.com": "ashby",
    "apply.workable.com": "workable",
    "jobs.smartrecruiters.com": "smartrecruiters",
    "myworkdayjobs.com": "workday",
    "recruitee.com": "recruitee",
}


def detect_ats(url):
    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    for h, ats in ATS_HOSTS.items():
        if host == h or host.endswith("." + h) or h in host:
            return ats
    # greenhouse-backed employer pages carry gh_jid
    if "gh_jid=" in (url or "") or "gh_src=" in (url or ""):
        return "greenhouse"
    return "other"


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: apply_li_resolution.py <results.json>")
    results = json.load(open(sys.argv[1]))
    cfg = load()
    conn = db.connect(cfg["_db_path"])
    # map linkedin numeric id -> db row id via the url
    off = easy = miss = skip = 0
    for r in results:
        jid = str(r.get("jid") or r.get("id") or "")
        row = conn.execute(
            "SELECT id, status FROM jobs WHERE url LIKE ? AND ats='linkedin'"
            " LIMIT 1", ("%/jobs/view/" + jid + "%",)).fetchone()
        if not row:
            miss += 1
            continue
        if row["status"] in ("applied", "applying"):
            skip += 1
            continue
        url = r.get("offsite")
        if url:
            ats = detect_ats(url)
            cu = canonical_url(url)
            dup = conn.execute(
                "SELECT id FROM jobs WHERE canonical_url=? AND id!=?",
                (cu, row["id"])).fetchone()
            if dup:
                db.set_status(conn, row["id"], "duplicate",
                              filter_reason="li-resolved to known %s" % ats)
            else:
                conn.execute(
                    "UPDATE jobs SET apply_url=?, ats=?, canonical_url=?,"
                    " status='discovered', filter_reason=NULL, updated_at=?"
                    " WHERE id=?",
                    (url, ats, cu, db.utcnow(), row["id"]))
                off += 1
        elif r.get("easy"):
            db.set_status(conn, row["id"], "needs_human",
                          apply_notes="linkedin easy apply only; needs a "
                          "throttled logged-in pass")
            easy += 1
        else:
            miss += 1
    conn.commit()
    print("resolved offsite=%d easy_only=%d no_method=%d skipped=%d"
          % (off, easy, miss, skip))


if __name__ == "__main__":
    main()

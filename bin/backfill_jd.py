#!/usr/bin/env python
"""Backfill job descriptions for rows parked in 'needs_jd'.

require_jd fails closed, so every job that arrived without a description
(nearly all LinkedIn/JobSpy results) never reaches the scorer. This fetches
the description from the source and returns the row to 'discovered' so the
next match run can score it.

    bin/backfill_jd.py --limit 500 [--workers 8]

LinkedIn descriptions come from the public jobs-guest endpoint, which needs no
login. Everything else is fetched from its own URL and stripped to text.
"""
import argparse
import concurrent.futures as cf
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobpilot import db                       # noqa: E402
from jobpilot.config import load              # noqa: E402
from jobpilot.normalize import html_to_text   # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
LI_GUEST = ("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/%s")
LI_ID = re.compile(r"/jobs/view/(?:[^/]*-)?(\d{6,})")


def fetch(url, timeout=20, retries=2):
    """GET with a backoff on 429. LinkedIn's guest endpoint throttles hard
    once a few hundred requests go through, and a bare raise_for_status()
    turns that into a permanently empty description."""
    for attempt in range(retries + 1):
        r = requests.get(url, headers={"User-Agent": UA,
                                       "Accept-Language": "en-US,en;q=0.9"},
                         timeout=timeout)
        if r.status_code == 429 and attempt < retries:
            time.sleep(30 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.text
    return ""


def jd_for(job):
    """Return description text for one job, or '' when it cannot be had."""
    url = job["apply_url"] or job["url"] or ""
    if "linkedin.com" in url:
        m = LI_ID.search(url)
        if not m:
            return ""
        html = fetch(LI_GUEST % m.group(1))
        # the guest endpoint returns a fragment; the description lives in
        # .description__text / .show-more-less-html__markup
        m2 = re.search(
            r'class="[^"]*(?:show-more-less-html__markup|description__text)'
            r'[^"]*"[^>]*>(.*?)</section>', html, re.S)
        return html_to_text(m2.group(1) if m2 else html)
    if not url:
        return ""
    return html_to_text(fetch(url))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--min-chars", type=int, default=200)
    args = ap.parse_args()

    cfg = load()
    conn = db.connect(cfg["_db_path"])
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status='needs_jd' ORDER BY rowid DESC LIMIT ?",
        (args.limit,)).fetchall()
    print("backfilling %d jobs" % len(rows), flush=True)

    filled = failed = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(jd_for, j): j for j in rows}
        for fut in cf.as_completed(futures):
            j = futures[fut]
            try:
                jd = fut.result()
            except Exception:
                jd = ""
            if len(jd) >= args.min_chars:
                conn.execute(
                    "UPDATE jobs SET jd_text=?, status='discovered',"
                    " filter_reason=NULL, updated_at=? WHERE id=?",
                    (jd[:20000], db.utcnow(), j["id"]))
                filled += 1
            else:
                failed += 1
            if (filled + failed) % 25 == 0:
                conn.commit()
                print("  %d filled / %d empty" % (filled, failed), flush=True)
    conn.commit()
    print("done: %d filled, %d still without a description" % (filled, failed))


if __name__ == "__main__":
    main()

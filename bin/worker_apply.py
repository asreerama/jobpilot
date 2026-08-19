#!/usr/bin/env python
"""One parallel apply worker.

Claims jobs from the queue atomically and hands each to Claude through
applier.apply_one. Each worker points JOBPILOT_MCP_CONFIG at its own
Playwright MCP config so the browser profiles never collide.

Claim fencing: every claim stamps claim_pid,
claim_worker, claim_attempt and lease_until onto the row. The supervisor may
requeue a row only when its owning PID is dead, and the final status write in
applier.apply_one refuses to overwrite terminal rows, so a late-finishing
attempt cannot double-record and a requeued job cannot be double-claimed.

    bin/worker_apply.py --worker 3 --count 5 [--ats greenhouse,ashby]
"""
import argparse
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobpilot import db                      # noqa: E402
from jobpilot.applier import apply_one, _set_backoff  # noqa: E402
from jobpilot.config import load             # noqa: E402

LEASE_MIN = 45


def _ats_clause(ats_filter, exclude_ats):
    sql, args = "", []
    if ats_filter:
        sql += " AND ats IN (%s)" % ",".join("?" * len(ats_filter))
        args += ats_filter
    if exclude_ats:
        # NULL/'' ats must not vanish into NOT IN; route them through 'other'
        sql += (" AND COALESCE(NULLIF(ats,''),'other') NOT IN (%s)"
                % ",".join("?" * len(exclude_ats)))
        args += exclude_ats
    return sql, args


def claim(conn, worker_n, ats_filter, exclude_ats):
    """Atomically take the highest-scoring queued job for this worker."""
    clause, args = _ats_clause(ats_filter, exclude_ats)
    row = conn.execute(
        "SELECT * FROM jobs WHERE status='queued'" + clause +
        " ORDER BY score DESC, COALESCE(posted_at,'') DESC LIMIT 1",
        args).fetchone()
    if not row:
        return None
    lease = (datetime.now(timezone.utc) + timedelta(minutes=LEASE_MIN)
             ).strftime("%Y-%m-%dT%H:%M:%SZ")
    attempt = uuid.uuid4().hex[:12]
    cur = conn.execute(
        "UPDATE jobs SET status='applying', updated_at=?, claim_pid=?,"
        " claim_worker=?, claim_attempt=?, lease_until=?"
        " WHERE id=? AND status='queued'",
        (db.utcnow(), os.getpid(), worker_n, attempt, lease, row["id"]))
    conn.commit()
    if cur.rowcount != 1:
        return None            # another worker won the race; caller retries
    # Company+title dedupe against rows already applied: offices/reposts of
    # the same role carry different URLs, so the URL uniqueness in the schema
    # is not enough.
    dup = conn.execute(
        "SELECT 1 FROM jobs WHERE status='applied' AND lower(company)="
        "lower(?) AND lower(title)=lower(?) AND id != ?",
        (row["company"] or "", row["title"] or "", row["id"])).fetchone()
    if dup:
        db.set_status(conn, row["id"], "duplicate",
                      filter_reason="company+title already applied")
        conn.commit()
        return "duplicate"
    return row


def mine_left(conn, ats_filter, exclude_ats) -> bool:
    """Any queued job this worker is actually allowed to take?"""
    clause, args = _ats_clause(ats_filter, exclude_ats)
    return conn.execute("SELECT 1 FROM jobs WHERE status='queued'" + clause +
                        " LIMIT 1", args).fetchone() is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", type=int, required=True)
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--ats", default="")
    ap.add_argument("--exclude-ats", default="")
    args = ap.parse_args()

    cfg = load()
    conn = db.connect(cfg["_db_path"])
    root = cfg["_root"]
    mcp = os.path.join(root, "workers", "pw-%d.json" % args.worker)
    if not os.path.exists(mcp):
        sys.exit("missing MCP config %s" % mcp)
    os.environ["JOBPILOT_MCP_CONFIG"] = mcp

    ats = [x for x in args.ats.split(",") if x]
    excl = [x for x in args.exclude_ats.split(",") if x]
    done = waited = 0
    while done < args.count:
        job = claim(conn, args.worker, ats, excl)
        if job == "duplicate":
            continue
        if job is None:
            # "Is there anything left FOR ME" must honour this worker's own
            # ats filters. Checking the whole queue instead spins every
            # worker forever against a queue of entirely excluded ATSes.
            if mine_left(conn, ats, excl):
                waited += 1
                if waited > 30:        # ~1 min of losing every race: give up
                    print("worker %d: contention, exiting after %d"
                          % (args.worker, done))
                    break
                time.sleep(2)
                continue
            print("worker %d: nothing queued for this worker after %d"
                  % (args.worker, done))
            break
        waited = 0
        print("worker %d: applying %s %s - %s (%s, score %s)" % (
            args.worker, job["id"], job["company"], job["title"], job["ats"],
            job["score"]), flush=True)
        outcome = apply_one(conn, cfg, job)
        print("worker %d: %s -> %s" % (args.worker, job["id"], outcome),
              flush=True)
        if outcome == "usage_limit":
            # Make the hold global immediately: without the marker the other
            # five workers burn through their claims discovering the same
            # thing.
            _set_backoff(root, cfg["apply"].get(
                "usage_limit_backoff_minutes", 90))
            print("worker %d: usage limit; backoff set, exiting" % args.worker)
            break
        done += 1


if __name__ == "__main__":
    main()

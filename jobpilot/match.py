"""Matching: hard filters, then LLM scoring, then queueing."""
import json
import traceback

from . import db
from .filters import Filters
from .score import score_batch, is_usage_limit


def run(conn, cfg):
    filters = Filters(cfg)
    sc = cfg["scoring"]
    stats = {"filtered_out": 0, "skipped_no_sponsorship": 0, "duplicate": 0,
             "flagged_referral": 0, "referral_wanted": 0, "scored": 0,
             "queued": 0, "score_errors": 0}

    import os
    rows = conn.execute("SELECT * FROM jobs WHERE status='discovered'").fetchall()
    limit = os.environ.get("JOBPILOT_MATCH_LIMIT")
    survivors = []
    referral_ids = set()
    for j in rows:
        verdict, reason = filters.check(j)
        if verdict == "pass":
            survivors.append(j)
            if reason == "referral_wanted":
                referral_ids.add(j["id"])
                stats["referral_wanted"] += 1
        else:
            db.set_status(conn, j["id"], verdict, filter_reason=reason)
            stats[verdict] += 1
    conn.commit()

    if limit:
        survivors = survivors[: int(limit)]
    for i in range(0, len(survivors), sc["batch_size"]):
        batch = survivors[i: i + sc["batch_size"]]
        try:
            results = score_batch(batch, cfg)
        except Exception as e:
            stats["score_errors"] += 1
            if is_usage_limit(str(e)):
                stats["usage_limited"] = True
                break  # leave the rest as 'discovered'; next run picks them up
            continue
        for j in batch:
            r = results.get(j["id"])
            if not r:
                continue
            if r["sponsorship_risk"] == "stated_no":
                db.set_status(conn, j["id"], "skipped_no_sponsorship",
                              score=r["score"], score_reasons=r["reason"],
                              sponsorship="no")
                stats["skipped_no_sponsorship"] += 1
                continue
            status = "queued" if r["score"] >= sc["apply_threshold"] else "scored"
            extra = ({"filter_reason": "referral_wanted"}
                     if j["id"] in referral_ids else {})
            db.set_status(conn, j["id"], status, score=r["score"],
                          score_reasons=r["reason"],
                          sponsorship={"none_seen": "unknown",
                                       "hinted_no": "risky"}[
                                           r["sponsorship_risk"]]
                          if r["sponsorship_risk"] != "stated_no" else "no",
                          **extra)
            stats["queued" if status == "queued" else "scored"] += 1
        conn.commit()
    return stats


def main():
    from .config import load
    cfg = load()
    conn = db.connect(cfg["_db_path"])
    started = db.utcnow()
    ok = True
    try:
        stats = run(conn, cfg)
    except Exception:
        ok = False
        stats = {"fatal": traceback.format_exc(limit=3)}
    db.log_run(conn, "match", started, ok, json.dumps(stats, default=str))
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()

"""Parallel apply mode: no inter-job gaps, no batch cap, N concurrent
claude -p workers, each on its own isolated Playwright
browser (--mcp-config pw-isolated.json) so they cannot fight over the shared
persistent Chrome profile.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import db, notify
from .applier import build_prompt
from .config import load
from .score import is_usage_limit

WORKERS = 4
TIMEOUT_S = 1800

PARALLEL_NOTE = """

PARALLEL RUN NOTE: several applications are running concurrently on this
machine. Use ONLY the mcp__pw__* browser tools (an isolated browser dedicated
to this run). NEVER use mcp__plugin_playwright_playwright__* tools. Your
browser starts with a fresh empty profile (no logins, no tabs to preserve -
the "never touch tab 0" rule does not apply, use the initial tab freely).
"""

stop_dispatch = threading.Event()
print_lock = threading.Lock()


def log(msg):
    with print_lock:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


ICON = {"applied": "✓", "needs_human": "⚠", "failed": "✗", "timeout": "✗",
        "skipped_no_sponsorship": "⊘", "expired": "⊘", "usage_limit": "⏸",
        "not_started": "⏸", "skipped_not_queued": "⊘"}
# Outcomes worth a line of their own on the phone. The rest are just noise.
DETAIL = ("applied", "needs_human", "failed", "timeout")
MAX_LINES = 12


def summarize(results, elapsed_s):
    """Build the (title, body, priority, tags) of the end-of-run push."""
    n = {}
    for r in results:
        n[r["outcome"]] = n.get(r["outcome"], 0) + 1
    applied = n.get("applied", 0)
    broken = n.get("failed", 0) + n.get("timeout", 0) + \
        sum(v for k, v in n.items() if k.startswith("error:"))
    needs = n.get("needs_human", 0)

    if n.get("usage_limit"):
        title, tags, prio = ("⏸ JobPilot stopped early", ["pause_button"],
                             notify.P_HIGH)
    elif applied:
        title, tags, prio = (f"✅ JobPilot: {applied} applied",
                             ["white_check_mark"], notify.P_DEFAULT)
    elif not results:
        title, tags, prio = ("💤 JobPilot: nothing queued", ["zzz"],
                             notify.P_LOW)
    else:
        title, tags, prio = ("⚠️ JobPilot: 0 applied", ["warning"],
                             notify.P_HIGH)

    mins = max(1, round(elapsed_s / 60))
    head = f"{mins}m · " + " · ".join(
        f"{v} {k.replace('_', ' ')}" for k, v in sorted(
            n.items(), key=lambda kv: -kv[1]))
    lines = [head]

    detail = [r for r in results
              if r["outcome"] in DETAIL or r["outcome"].startswith("error:")]
    detail.sort(key=lambda r: DETAIL.index(r["outcome"])
                if r["outcome"] in DETAIL else 99)
    if detail:
        lines.append("")
    for r in detail[:MAX_LINES]:
        icon = ICON.get(r["outcome"], "✗")
        note = (r["note"] or "").replace("\n", " ").strip()
        tail = f" — {note[:90]}" if note and r["outcome"] != "applied" else ""
        lines.append(f"{icon} {r['company']} · {r['title']}{tail}")
    if len(detail) > MAX_LINES:
        lines.append(f"…and {len(detail) - MAX_LINES} more")

    return title, "\n".join(lines), prio, tags


def apply_job(job, idx, cfg):
    def res(outcome, note=""):
        return {"id": job["id"], "company": job["company"],
                "title": job["title"], "outcome": outcome, "note": note}

    if idx < WORKERS:
        time.sleep(idx * 15)  # stagger the first wave of browser launches
    if stop_dispatch.is_set():
        return res("not_started")
    a = cfg["apply"]
    mcp_config = os.path.join(cfg["_root"], "pw-isolated.json")
    workdir = cfg["_root"]
    conn = db.connect(cfg["_db_path"])
    try:
        cur = conn.execute("SELECT status FROM jobs WHERE id=?",
                           (job["id"],)).fetchone()
        if not cur or cur["status"] != "queued":
            return res("skipped_not_queued")
        prompt = build_prompt(job, cfg) + PARALLEL_NOTE
        db.set_status(conn, job["id"], "applying")
        conn.commit()
        log(f"START {job['company']} - {job['title']} ({job['ats']})")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        try:
            proc = subprocess.run(
                [a["claude_bin"], "-p", "--model", a["model"],
                 "--permission-mode", "bypassPermissions",
                 "--mcp-config", mcp_config,
                 "--disallowedTools", "mcp__plugin_playwright_playwright__*"],
                input=prompt, capture_output=True, text=True,
                timeout=TIMEOUT_S, env=env, cwd=workdir,
            )
        except subprocess.TimeoutExpired:
            db.set_status(conn, job["id"], "needs_human",
                          apply_notes=f"parallel run timed out after "
                                      f"{TIMEOUT_S}s")
            conn.commit()
            log(f"TIMEOUT {job['company']} - {job['title']}")
            return res("timeout", f"no response after {TIMEOUT_S // 60}m")

        out = proc.stdout or ""
        err = proc.stderr or ""
        if is_usage_limit(out[-2000:]) or is_usage_limit(err[-2000:]):
            db.set_status(conn, job["id"], "queued")
            conn.commit()
            first_hit = not stop_dispatch.is_set()
            stop_dispatch.set()
            log(f"USAGE LIMIT on {job['company']}; requeued, halting dispatch")
            if first_hit:  # one alert per run, not one per worker
                notify.push(
                    "⛔️ JobPilot halted — usage limit",
                    f"Hit the plan window on {job['company']}. In-flight jobs "
                    f"requeued, dispatch stopped. Nothing else applies until "
                    f"the window resets.",
                    notify.P_URGENT, ["octagonal_sign"])
            return res("usage_limit")

        m = None
        for m in re.finditer(r"OUTCOME_JSON:\s*(\{.*?\})", out):
            pass  # keep the last occurrence
        if m:
            try:
                o = json.loads(m.group(1))
                outcome = o.get("outcome", "failed")
                note = (o.get("note") or "")[:300]
            except ValueError:
                outcome, note = "needs_human", "unparseable OUTCOME_JSON"
        elif proc.returncode != 0:
            outcome, note = "failed", f"rc={proc.returncode} {err[:200]}"
        else:
            outcome, note = "needs_human", "run ended without OUTCOME_JSON"

        if outcome not in ("applied", "skipped_no_sponsorship", "needs_human",
                           "expired", "failed"):
            outcome = "needs_human"
        # the worker may already have set status=applied itself via sqlite3;
        # only overwrite rows still stuck in 'applying'
        cur = conn.execute("SELECT status FROM jobs WHERE id=?",
                           (job["id"],)).fetchone()
        if cur and cur["status"] == "applying":
            fields = {"apply_notes": note}
            if outcome == "applied":
                fields["applied_at"] = db.utcnow()
            db.set_status(conn, job["id"], outcome, **fields)
            conn.commit()
        else:
            outcome = cur["status"] if cur else outcome
        log(f"DONE {job['company']} - {job['title']}: {outcome} | {note[:120]}")
        return res(outcome, note)
    finally:
        conn.close()


def main():
    cfg = load()
    conn = db.connect(cfg["_db_path"])
    started = db.utcnow()
    jobs = [dict(r) for r in conn.execute(
        "SELECT * FROM jobs WHERE status='queued'"
        " ORDER BY score DESC, COALESCE(posted_at,'') DESC").fetchall()]
    seen = {((r["company"] or "").lower(), (r["title"] or "").lower())
            for r in conn.execute(
                "SELECT company, title FROM jobs WHERE status IN"
                " ('applied','applying')")}
    batch = []
    for j in jobs:
        pair = ((j["company"] or "").lower(), (j["title"] or "").lower())
        if pair in seen:
            db.set_status(conn, j["id"], "duplicate",
                          filter_reason="company+title already applied")
            conn.commit()
            log(f"DUP {j['company']} - {j['title']}")
            continue
        seen.add(pair)
        batch.append(j)
    log(f"dispatching {len(batch)} jobs across {WORKERS} workers")

    t0 = time.time()
    preview = "\n".join(f"· {j['company']} · {j['title']}" for j in batch[:6])
    if len(batch) > 6:
        preview += f"\n…and {len(batch) - 6} more"
    notify.push(
        f"🚀 JobPilot started — {len(batch)} queued",
        f"{WORKERS} workers dispatching\n\n{preview}" if batch
        else "Queue is empty, nothing to apply to.",
        notify.P_DEFAULT if batch else notify.P_LOW, ["rocket"])

    stats, results = {}, []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(apply_job, j, i, cfg): j
                for i, j in enumerate(batch)}
        for f in as_completed(futs):
            j = futs[f]
            try:
                r = f.result()
            except Exception as e:  # noqa: BLE001
                r = {"id": j["id"], "company": j["company"],
                     "title": j["title"],
                     "outcome": f"error:{type(e).__name__}",
                     "note": str(e)[:200]}
                log(f"WORKER ERROR {j['company']}: {e}")
            results.append(r)
            stats[r["outcome"]] = stats.get(r["outcome"], 0) + 1
    db.log_run(conn, "apply-parallel", started, True, json.dumps(stats))
    conn.close()

    title, body, prio, tags = summarize(results, time.time() - t0)
    notify.push(title, body, prio, tags)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()

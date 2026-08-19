"""Apply stage: hands each queued job to Claude (subscription CLI) which drives
the job-applications skill + Playwright end to end. Strictly serial - the
Playwright browser profile is shared.
"""
import json
import os
import random
import re
import subprocess
import time
import traceback
from datetime import datetime, timedelta

from . import db
from .score import is_usage_limit

BACKOFF_MARKER = "logs/usage-limit-until"

PROMPT = """Apply to this job end to end, fully autonomously. Nobody is \
watching; never wait for confirmation. First read APPLY_PROTOCOL.md in the \
working directory and follow it exactly: duplicate check against \
applications.csv, JD sponsorship scan, tailored resume (follow \
skills/resume-tailoring/SKILL.md; render with resume/render_tex.py), \
Playwright form fill, screenshot review before submit, review record, \
log to applications.csv.

Job: {title} at {company}
Apply URL: {apply_url}
ATS: {ats}
Fit notes from the scorer: {reasons}

If the apply URL is a linkedin.com link: do NOT apply through LinkedIn. Find
the employer's own posting first - use WebSearch ("{company} {title} greenhouse
OR lever OR ashby OR careers") or check the company careers page - and apply
on the company ATS directly. Verify it is the same role (title + location).
Only if no direct posting exists anywhere, use the LinkedIn page itself.

CANDIDATE PROFILE AND FIXED ANSWERS (follow these exactly):
{candidate_rules}

Additional rules for this autonomous run:
- If the JD or the rendered form disqualifies the candidate on a hard gate from
the profile above (e.g. the employer will not sponsor and the candidate needs
sponsorship), abandon without submitting and use outcome
"skipped_no_sponsorship".
- If blocked by a captcha, a login wall you cannot pass, or email verification, \
leave the browser tab as-is and use outcome "needs_human" with a precise note.
- Workday: credentials live at secrets/workday.json (email + standard \
password). Sign in if the tenant knows the email, otherwise create the account \
with those credentials. If it demands an emailed verification code, stop -> \
"needs_human".
- If the posting is gone/closed, outcome "expired".
- Any other unrecoverable problem, outcome "failed" with the reason.

REVIEW RECORD (required): before clicking Submit, write
out/applications/{job_id}-{company_slug}.md containing: job title,
company, URL, resume PDF path used, then EVERY form field with the exact answer
you entered (including dropdowns/radio selections), and any free-text answers in
full. Save the pre-submit full-page screenshot next to it as
{job_id}-{company_slug}.png. Do this even when the outcome is not "applied" -
record how far you got. The candidate reviews these files later.

End your final message with exactly one line (nothing after it):
OUTCOME_JSON: {{"outcome": "applied|skipped_no_sponsorship|needs_human|expired|failed", "note": "<=200 chars"}}
"""


def build_prompt(job, cfg) -> str:
    return PROMPT.format(
        title=job["title"], company=job["company"],
        apply_url=job["apply_url"] or job["url"], ats=job["ats"],
        reasons=job["score_reasons"] or "",
        job_id=job["id"],
        company_slug=job["company_slug"] or
        re.sub(r"[^a-z0-9]+", "-", (job["company"] or "x").lower())[:30],
        candidate_rules=cfg.get("_candidate_rules")
        or "(profile.md is missing - create it from profile.example.md)",
    )


def _today_count(conn) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COUNT(*) c FROM jobs WHERE status IN ('applied','applying')"
        " AND substr(COALESCE(applied_at, updated_at),1,10) >= ?", (today,)
    ).fetchone()
    return row["c"]


def _backoff_active(root) -> bool:
    path = os.path.join(root, BACKOFF_MARKER)
    try:
        with open(path) as f:
            until = datetime.fromisoformat(f.read().strip())
        if datetime.now() < until:
            return True
        os.remove(path)
    except (FileNotFoundError, ValueError):
        pass
    return False


def _set_backoff(root, minutes):
    until = datetime.now() + timedelta(minutes=minutes)
    with open(os.path.join(root, BACKOFF_MARKER), "w") as f:
        f.write(until.isoformat())


def _recover_stale(conn, timeout_s):
    """A crashed run leaves rows stuck in 'applying'."""
    cutoff = (datetime.utcnow() - timedelta(seconds=timeout_s * 2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    for r in conn.execute(
            "SELECT id FROM jobs WHERE status='applying' AND updated_at < ?",
            (cutoff,)).fetchall():
        db.set_status(conn, r["id"], "needs_human",
                      apply_notes="stale applying state; run likely crashed")
    conn.commit()


def apply_one(conn, cfg, job) -> str:
    a = cfg["apply"]
    prompt = build_prompt(job, cfg)
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    workdir = cfg["_root"]
    db.set_status(conn, job["id"], "applying")
    conn.commit()
    try:
        # Parallel workers set JOBPILOT_MCP_CONFIG to their own Playwright
        # MCP config so each worker drives a private Chrome profile. Without
        # it the shared persistent profile is used and runs must stay serial.
        cmd = [a["claude_bin"], "-p", "--model", a["model"],
               "--permission-mode", "bypassPermissions"]
        mcp_cfg = os.environ.get("JOBPILOT_MCP_CONFIG")
        if mcp_cfg:
            cmd += ["--mcp-config", mcp_cfg, "--strict-mcp-config"]
        proc = subprocess.run(
            cmd,
            input=prompt, capture_output=True, text=True,
            timeout=a["per_job_timeout_s"], env=env, cwd=workdir,
        )
    except subprocess.TimeoutExpired:
        db.set_status(conn, job["id"], "needs_human",
                      apply_notes=f"apply run timed out after "
                                  f"{a['per_job_timeout_s']}s")
        conn.commit()
        return "timeout"

    out = proc.stdout or ""
    err = proc.stderr or ""
    if is_usage_limit(out[-2000:]) or is_usage_limit(err[-2000:]):
        db.set_status(conn, job["id"], "queued")  # requeue untouched
        conn.commit()
        return "usage_limit"

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

    fields = {"apply_notes": note}
    if outcome == "applied":
        fields["applied_at"] = db.utcnow()
    if outcome not in ("applied", "skipped_no_sponsorship", "needs_human",
                       "expired", "failed"):
        outcome = "needs_human"
    # Guarded write: if the supervisor requeued this row and another attempt
    # already finished it, rowcount is 0 and this late result is dropped
    # rather than overwriting a terminal record.
    changed = db.set_status(conn, job["id"], outcome, **fields)
    conn.commit()
    if not changed:
        return "late_result_dropped"
    return outcome


def run(conn, cfg):
    a = cfg["apply"]
    root = cfg["_root"]
    stats = {"attempted": 0}
    if _backoff_active(root):
        return {"skipped": "usage-limit backoff active"}
    _recover_stale(conn, a["per_job_timeout_s"])

    max_day = int(os.environ.get("JOBPILOT_MAX_PER_DAY", a["max_per_day"]))
    max_batch = int(os.environ.get("JOBPILOT_BATCH", a["max_per_batch"]))
    done_today = _today_count(conn)
    room = max_day - done_today
    if room <= 0:
        return {"skipped": f"daily cap reached ({done_today})"}

    auto = a.get("auto_ats", [])
    secrets_ok = os.path.exists(os.path.join(root, a.get("workday_secrets", "")))
    jobs = conn.execute(
        "SELECT * FROM jobs WHERE status='queued' ORDER BY score DESC,"
        " COALESCE(posted_at,'') DESC LIMIT ?",
        (min(max_batch, room),)).fetchall()

    seen_pairs = {
        ((r["company"] or "").lower(), (r["title"] or "").lower())
        for r in conn.execute(
            "SELECT company, title FROM jobs WHERE status IN"
            " ('applied','applying')")}
    today = datetime.now().strftime("%Y-%m-%d")
    li_done = conn.execute(
        "SELECT COUNT(*) c FROM jobs WHERE ats='linkedin' AND status='applied'"
        " AND substr(applied_at,1,10) >= ?", (today,)).fetchone()["c"]
    li_cap = a.get("linkedin_max_per_day", 3)
    for job in jobs:
        # the batch list is a snapshot; skip anything reclassified since
        cur = conn.execute("SELECT status FROM jobs WHERE id=?",
                           (job["id"],)).fetchone()
        if not cur or cur["status"] != "queued":
            continue
        if job["ats"] == "linkedin":
            if li_done >= li_cap:
                continue  # leave queued for tomorrow's cap
            li_done += 1
        pair = ((job["company"] or "").lower(), (job["title"] or "").lower())
        if pair in seen_pairs:  # same role posted per-office; apply once
            db.set_status(conn, job["id"], "duplicate",
                          filter_reason="company+title already applied")
            conn.commit()
            continue
        seen_pairs.add(pair)
        if job["ats"] not in auto:
            db.set_status(conn, job["id"], "needs_human",
                          apply_notes=f"ats {job['ats']} not automated")
            conn.commit()
            continue
        if job["ats"] == "workday" and not secrets_ok:
            db.set_status(conn, job["id"], "needs_human",
                          apply_notes="workday credentials file missing")
            conn.commit()
            continue
        outcome = apply_one(conn, cfg, job)
        stats["attempted"] += 1
        stats[outcome] = stats.get(outcome, 0) + 1
        if outcome == "usage_limit":
            _set_backoff(root, a.get("usage_limit_backoff_minutes", 90))
            stats["backoff_set"] = True
            break
        gap = int(os.environ.get("JOBPILOT_GAP_S", a["min_gap_minutes"] * 60))
        time.sleep(gap + random.randint(0, max(1, gap // 2)))
    return stats


def dry_run(conn, cfg):
    a = cfg["apply"]
    jobs = conn.execute(
        "SELECT * FROM jobs WHERE status='queued' ORDER BY score DESC,"
        " COALESCE(posted_at,'') DESC LIMIT ?", (a["max_per_batch"],)).fetchall()
    for job in jobs:
        print(f"WOULD APPLY [{job['score']}] {job['company']} - {job['title']}"
              f" ({job['ats']})\n  {job['apply_url'] or job['url']}")
    if jobs:
        print("\n--- prompt for first job ---")
        print(build_prompt(jobs[0], cfg))
    return {"dry_run": len(jobs)}


def main():
    import sys
    from .config import load
    from .lock import acquire
    cfg = load()
    _lk = acquire("apply", cfg["_root"])
    conn = db.connect(cfg["_db_path"])
    started = db.utcnow()
    ok = True
    try:
        if "--dry-run" in sys.argv:
            stats = dry_run(conn, cfg)
            print(json.dumps(stats))
            return
        stats = run(conn, cfg)
    except Exception:
        ok = False
        stats = {"fatal": traceback.format_exc(limit=3)}
    db.log_run(conn, "apply", started, ok, json.dumps(stats, default=str))
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()

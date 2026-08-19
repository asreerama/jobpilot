#!/usr/bin/env python
"""Keeps the apply fleet alive.

Layers:
  launchd (KeepAlive) -> supervisor.sh -> this -> N worker_apply.py workers

What each 60s cycle does:
  1. reap dead workers; respawn with a per-slot circuit breaker
  2. requeue expired-lease 'applying' rows, but only when the owning PID is
     dead AND no review record exists (a record means the run reached the
     form; treat as ambiguous -> needs_human, never auto-retry a possible
     double submission)
  3. compute applyable (queued minus excluded ATS, NULL-safe)
  4. queue dry -> run match synchronously (locked, timed out, result logged);
     still dry -> promote scored >= promote_floor
  5. write heartbeat atomically with an explicit state string; the watchdog
     keys off state, never off raw counts
  6. self-restart when watched source files change on disk: exit 0, launchd
     relaunches with fresh code (validated by py_compile first, debounced)

Progress signal: the newest terminal-status transition (applied OR failed OR
needs_human OR skipped OR expired). applied_today alone reads a healthy fleet
working a bad batch as a stall.
"""
import argparse
import os
import py_compile
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobpilot import db                       # noqa: E402
from jobpilot.config import load              # noqa: E402
from jobpilot.lock import acquire             # noqa: E402

HEARTBEAT = "logs/supervisor-heartbeat"
IDLE_SLEEP_S = 60
MATCH_COOLDOWN_MIN = 20
MATCH_TIMEOUT_S = 1200
RESTART_DEBOUNCE_S = 300
WATCHED = ("bin/supervisor.py", "bin/worker_apply.py",
           "jobpilot/applier.py", "jobpilot/db.py", "config.yaml")
TERMINAL_SET = "('applied','failed','needs_human','skipped_no_sponsorship'," \
               "'expired','duplicate')"


def log(msg):
    print("%s %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def notify(title, body, priority=3):
    try:
        from jobpilot import notify as n
        ok = n.push(title, body, priority, tags="supervisor")
        if not ok:
            log("ALERT DELIVERY FAILED: %s" % title)
    except Exception as e:
        log("ALERT DELIVERY FAILED: %s (%s)" % (title, e))


def pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def utc_now():
    return datetime.now(timezone.utc)


def recover_expired_leases(conn, root):
    """Requeue 'applying' rows whose lease expired AND whose owner is dead.

    Ambiguity rule: if the worker got far enough to write its review record,
    the form may have been submitted; that job goes to needs_human with a
    note instead of being retried."""
    now = utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        "SELECT id, company, company_slug, claim_pid FROM jobs"
        " WHERE status='applying' AND lease_until IS NOT NULL"
        " AND lease_until < ?", (now,)).fetchall()
    requeued = parked = 0
    for r in rows:
        if pid_alive(r["claim_pid"]):
            continue           # owner still running; let it finish
        slug = r["company_slug"] or ""
        rec = os.path.join(root, "out", "applications",
                           "%s-%s.md" % (r["id"], slug))
        if slug and os.path.exists(rec):
            db.set_status(conn, r["id"], "needs_human",
                          apply_notes="ambiguous: worker died after writing "
                          "review record; may already be submitted")
            parked += 1
        else:
            db.set_status(conn, r["id"], "queued",
                          apply_notes="requeued: lease expired, owner dead",
                          claim_pid=None, claim_attempt=None,
                          lease_until=None)
            requeued += 1
    # Legacy rows claimed before lease stamping existed (claim_pid NULL):
    # fall back to the old updated_at rule. No PID to check, so require a
    # much older age before touching them.
    cutoff = (utc_now() - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in conn.execute(
            "SELECT id FROM jobs WHERE status='applying'"
            " AND lease_until IS NULL AND updated_at < ?", (cutoff,)):
        db.set_status(conn, r["id"], "queued",
                      apply_notes="requeued: pre-lease claim, stale >90min")
        requeued += 1
    conn.commit()
    if requeued or parked:
        log("lease recovery: %d requeued, %d ambiguous->needs_human"
            % (requeued, parked))


def queued_applyable(conn, exclude):
    sql = "SELECT COUNT(*) c FROM jobs WHERE status='queued'"
    args = []
    ex = [x for x in exclude.split(",") if x]
    if ex:
        sql += (" AND COALESCE(NULLIF(ats,''),'other') NOT IN (%s)"
                % ",".join("?" * len(ex)))
        args += ex
    return conn.execute(sql, args).fetchone()["c"]


def counts(conn):
    return {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM jobs GROUP BY status")}


def applied_today(conn):
    # Local day expressed as a UTC range.
    local_midnight = datetime.now().replace(hour=0, minute=0, second=0,
                                            microsecond=0)
    start_utc = local_midnight.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return conn.execute(
        "SELECT COUNT(*) c FROM jobs WHERE status='applied'"
        " AND COALESCE(applied_at, updated_at) >= ?", (start_utc,)
    ).fetchone()["c"]


def last_terminal_ts(conn):
    r = conn.execute(
        "SELECT MAX(updated_at) m FROM jobs WHERE status IN " + TERMINAL_SET
    ).fetchone()
    return r["m"] or ""


def backoff_active(root):
    try:
        with open(os.path.join(root, "logs/usage-limit-until")) as f:
            return datetime.now() < datetime.fromisoformat(f.read().strip())
    except (FileNotFoundError, ValueError):
        return False


def write_heartbeat(root, state, **kv):
    """Atomic: tmp + rename, so the watchdog never reads a torn file."""
    path = os.path.join(root, HEARTBEAT)
    tmp = path + ".tmp"
    line = "%s state=%s %s\n" % (
        utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"), state,
        " ".join("%s=%s" % (k, v) for k, v in kv.items()))
    with open(tmp, "w") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def code_mtimes(root):
    out = {}
    for rel in WATCHED:
        try:
            out[rel] = os.stat(os.path.join(root, rel)).st_mtime
        except FileNotFoundError:
            out[rel] = 0
    return out


def code_changed(root, baseline):
    """Changed files must also compile; a half-written edit must not trigger
    a restart into a SyntaxError crash loop."""
    now = code_mtimes(root)
    changed = [r for r in WATCHED if now[r] != baseline[r]]
    if not changed:
        return False
    for rel in changed:
        if rel.endswith(".py"):
            try:
                py_compile.compile(os.path.join(root, rel), doraise=True)
            except py_compile.PyCompileError:
                return False   # mid-edit; check again next cycle
    return True


class Fleet:
    def __init__(self, root, size, batch, exclude):
        self.root = root
        self.size, self.batch, self.exclude = size, batch, exclude
        self.procs = {}
        self.crashes = {}      # slot -> [timestamps]
        self.disabled = set()

    def reap(self):
        for n in list(self.procs):
            p = self.procs[n]
            if p.poll() is None:
                continue
            del self.procs[n]
            if p.returncode not in (0, None):
                self.crashes.setdefault(n, []).append(time.time())
                recent = [t for t in self.crashes[n] if time.time() - t < 600]
                self.crashes[n] = recent
                if len(recent) >= 3:
                    self.disabled.add(n)
                    log("worker slot %d disabled: 3 crashes in 10 min" % n)
                    notify("JobPilot worker %d disabled" % n,
                           "3 crashes in 10 minutes; slot parked until "
                           "supervisor restart", 4)

    def ensure(self):
        started = 0
        for n in range(1, self.size + 1):
            if n in self.procs or n in self.disabled:
                continue
            mcp = os.path.join(self.root, "workers", "pw-%d.json" % n)
            if not os.path.exists(mcp):
                if n not in self.crashes:   # warn once
                    self.crashes[n] = []
                    log("worker slot %d has no MCP config, skipping" % n)
                continue
            logf = open(os.path.join(self.root,
                                     "logs/workers/w%d.log" % n), "a")
            self.procs[n] = subprocess.Popen(
                [sys.executable,
                 os.path.join(self.root, "bin/worker_apply.py"),
                 "--worker", str(n), "--count", str(self.batch),
                 "--exclude-ats", self.exclude],
                cwd=self.root, stdout=logf, stderr=subprocess.STDOUT)
            started += 1
        return started

    def stop(self):
        for p in self.procs.values():
            p.terminate()


def run_match(root, last_match):
    """Synchronous, locked by jobpilot.lock inside match itself via cycle, and
    timed out here. Popen-and-forget hides every match failure."""
    if last_match and datetime.now() - last_match < timedelta(
            minutes=MATCH_COOLDOWN_MIN):
        return last_match
    log("queue dry, running match (sync)")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "jobpilot.match"], cwd=root,
            capture_output=True, text=True, timeout=MATCH_TIMEOUT_S)
        tail = (proc.stdout or proc.stderr or "").strip()[-300:]
        log("match rc=%d %s" % (proc.returncode, tail))
        if proc.returncode != 0:
            notify("JobPilot match failed",
                   "rc=%d: %s" % (proc.returncode, tail[-180:]), 4)
    except subprocess.TimeoutExpired:
        log("match timed out after %ds" % MATCH_TIMEOUT_S)
        notify("JobPilot match timeout", "%ds" % MATCH_TIMEOUT_S, 4)
    return datetime.now()


def promote(conn, floor, exclude):
    ex = [x for x in exclude.split(",") if x]
    sql = ("UPDATE jobs SET status='queued', updated_at=? WHERE"
           " status='scored' AND score >= ?")
    args = [db.utcnow(), floor]
    if ex:
        sql += (" AND COALESCE(NULLIF(ats,''),'other') NOT IN (%s)"
                % ",".join("?" * len(ex)))
        args += ex
    n = conn.execute(sql, args).rowcount
    conn.commit()
    if n:
        log("promoted %d scored>=%d into queue" % (n, floor))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--max-per-day", type=int, default=0)
    ap.add_argument("--exclude-ats", default="workday,linkedin")
    ap.add_argument("--promote-floor", type=int, default=60)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    cfg = load()
    root = cfg["_root"]
    _lock = acquire("supervisor", root)   # single instance, keep ref
    conn = db.connect(cfg["_db_path"])
    cap = args.max_per_day or int(cfg["apply"].get("max_per_day", 40))
    fleet = Fleet(root, args.workers, args.batch, args.exclude_ats)
    baseline = code_mtimes(root)
    started_at = time.time()
    last_match = None
    promoted_once = False
    log("supervisor up: %d workers, batch %d, cap %d/day, floor %d"
        % (args.workers, args.batch, cap, args.promote_floor))

    import signal

    def graceful(_sig, _frm):
        log("SIGTERM: leaving workers to finish, exiting")
        write_heartbeat(root, "STOPPING")
        # workers are separate process groups; their claims finish and the
        # next supervisor's lease recovery covers any that die with us
        sys.exit(0)

    signal.signal(signal.SIGTERM, graceful)

    while True:
        fleet.reap()
        recover_expired_leases(conn, root)
        c = counts(conn)
        mine = queued_applyable(conn, args.exclude_ats)
        done = applied_today(conn)
        progress_ts = last_terminal_ts(conn)

        state = "RUNNING"
        if backoff_active(root):
            state = "BACKOFF"
            log("hold: usage-limit backoff")
        elif done >= cap:
            state = "CAP_HOLD"
            log("hold: daily cap %d/%d" % (done, cap))
        elif mine == 0:
            if c.get("discovered", 0):
                state = "MATCHING"
                last_match = run_match(root, last_match)
                promoted_once = False
            elif not promoted_once and c.get("scored", 0):
                state = "PROMOTING"
                if promote(conn, args.promote_floor, args.exclude_ats) == 0:
                    promoted_once = True   # nothing left above floor
            else:
                state = "DRY"
                log("dry: queued=%d (all excluded) scored-below-floor only"
                    % c.get("queued", 0))
        else:
            n = fleet.ensure()
            if n:
                log("started %d worker(s); applyable=%d" % (n, mine))

        write_heartbeat(root, state, applied_today=done,
                        queued=c.get("queued", 0), applyable=mine,
                        applying=c.get("applying", 0),
                        workers=len(fleet.procs),
                        last_terminal=progress_ts or "none",
                        pid=os.getpid())

        if code_changed(root, baseline) and \
                time.time() - started_at > RESTART_DEBOUNCE_S:
            log("watched source changed on disk; exiting for clean reload")
            write_heartbeat(root, "RELOADING")
            return   # rc 0 -> launchd KeepAlive relaunches fresh code

        if args.once:
            return
        time.sleep(IDLE_SLEEP_S)


if __name__ == "__main__":
    main()

#!/bin/zsh
# JobPilot healer: bounded self-fix loop, invoked by the watchdog after two
# mechanical restarts failed to restore progress. Runs claude -p with a
# tightly scoped mandate. Guardrails enforced HERE, not just in the prompt:
#   - internal-disk lock: one healer at a time
#   - max 2 runs per day; a failed run stands the healer down for the day
#   - 30 min hard timeout
#   - snapshots every code file before claude may touch anything (rollback)
#   - verification is NON-SUBMITTING: fresh heartbeat + a new claim appearing;
#     a real job application is never part of the test
GUARD="$HOME/.local/jobpilot-guard"
ROOT="${JOBPILOT_ROOT:-$HOME/jobpilot}"   # install.sh rewrites this line
LOG="$GUARD/healer.log"
LOCK="$GUARD/healer.lock"
DAYFILE="$GUARD/healer-runs-$(date '+%F')"
CLAUDE="${JOBPILOT_CLAUDE_BIN:-$HOME/.local/bin/claude}"
NOW=$(date '+%F %T')

say() { echo "[$NOW] $*" >> "$LOG"; }
alert() {
  local pri="$1" title="$2" body="$3"
  if [ -f "$HOME/.config/jobpilot/notify.sh" ]; then
    . "$HOME/.config/jobpilot/notify.sh"
    jp_notify "$title" "$body" "$pri" adhesive_bandage || say "ALERT-FAIL $title"
  else
    say "ALERT-NOCHAN $title: $body"
  fi
}

# single instance
exec 9>"$LOCK"
if ! flock -n 9 2>/dev/null; then
  # macOS zsh lacks flock; emulate with mkdir
  if ! mkdir "$LOCK.d" 2>/dev/null; then
    say "another healer running, exiting"; exit 0
  fi
  trap 'rmdir "$LOCK.d" 2>/dev/null' EXIT
fi

RUNS=$(cat "$DAYFILE" 2>/dev/null || echo 0)
if [ "$RUNS" -ge 2 ]; then
  say "daily healer budget spent ($RUNS); standing down"
  alert 5 "JobPilot: human needed" \
    "Watchdog restarts failed and the healer already ran $RUNS times today without fixing it."
  exit 0
fi
echo $((RUNS + 1)) > "$DAYFILE"

[ ! -d "$ROOT" ] && { say "pipeline root gone, cannot heal"; exit 0; }
[ ! -x "$CLAUDE" ] && { say "claude CLI missing"; alert 5 "JobPilot healer: claude CLI missing" "$CLAUDE not executable"; exit 0; }

# ---- snapshot for rollback ----------------------------------------------
STAMP=$(date '+%F-%H%M%S')
SNAP="$GUARD/snapshots/$STAMP"
mkdir -p "$SNAP"
cp -R "$ROOT/bin" "$SNAP/bin" 2>>"$LOG"
cp -R "$ROOT/jobpilot" "$SNAP/jobpilot" 2>>"$LOG"
cp "$ROOT/config.yaml" "$SNAP/config.yaml" 2>>"$LOG"
ls -t "$GUARD/snapshots" | tail -n +6 | while read -r d; do rm -rf "$GUARD/snapshots/$d"; done
say "run #$((RUNS + 1)) snapshot=$SNAP"

# ---- gather evidence -----------------------------------------------------
EVID="$GUARD/evidence-$STAMP.txt"
{
  echo "== supervisor log (last 120) =="; tail -120 "$ROOT/logs/supervisor.log" 2>/dev/null
  echo "== watchdog log (last 40) ==";  tail -40 "$GUARD/watchdog.log" 2>/dev/null
  echo "== heartbeat ==";               cat "$ROOT/logs/supervisor-heartbeat" 2>/dev/null
  echo "== launchd =="; launchctl print "gui/$(id -u)/com.jobpilot.supervisor" 2>&1 | grep -E "state|last exit|pid"
  echo "== status counts =="; sqlite3 "file:$ROOT/jobs.db?mode=ro" "select status,count(*) from jobs group by 1" 2>/dev/null
  echo "== worker logs =="; for i in 1 2 3 4 5 6; do echo "-- w$i"; tail -6 "$ROOT/logs/workers/w$i.log" 2>/dev/null; done
} > "$EVID"

PROMPT="You are the JobPilot healer. The apply pipeline on this Mac is stalled:
mechanical restarts by the watchdog did not restore progress. Evidence file:
$EVID  (read it first). Pipeline root: $ROOT

Diagnose the root cause and apply the SMALLEST fix that restores progress.

HARD RULES - violating any of these is worse than not fixing the stall:
- You may edit ONLY: files under $ROOT/bin/, $ROOT/jobpilot/, $ROOT/config.yaml.
- NEVER touch: jobs.db data rows (schema/status updates via the existing code
  paths only - no direct sqlite UPDATE/DELETE/DROP), applications.csv,
  $ROOT/secrets/, $ROOT/out/, anything outside $ROOT except launchctl
  kickstart of com.jobpilot.* jobs.
- Pre-edit snapshots exist at $SNAP - if your fix does not work, restore from
  there and say so.
- Do not start new long-running processes other than 'launchctl kickstart -k
  gui/UID/com.jobpilot.supervisor'.
- VERIFY without submitting anything: success = supervisor heartbeat file
  ($ROOT/logs/supervisor-heartbeat) fresh within 2 min AND state=RUNNING AND
  at least one row entering status='applying' within 10 min. Do NOT wait for
  a completed application and do NOT submit anything yourself.
- If the cause is outside your mandate (Claude usage limits, disk or volume
  hardware, ATS-side blocks), do NOT edit code; report that instead.

End with exactly one line:
HEALER_RESULT: {\"fixed\": true|false, \"diagnosis\": \"<=200 chars\", \"action\": \"<=200 chars\"}"

say "invoking claude"
OUT=$(echo "$PROMPT" | timeout 1800 "$CLAUDE" -p --model sonnet \
      --permission-mode bypassPermissions 2>>"$LOG")
RC=$?
echo "$OUT" | tail -40 >> "$LOG"

RESULT=$(echo "$OUT" | grep -o 'HEALER_RESULT: {.*}' | tail -1 | sed 's/HEALER_RESULT: //')
if [ -z "$RESULT" ]; then
  alert 5 "JobPilot healer: no result (rc=$RC)" \
    "Healer run produced no HEALER_RESULT. Human needed. Log: $LOG"
  exit 1
fi
FIXED=$(echo "$RESULT" | grep -o '"fixed": *true')
DIAG=$(echo "$RESULT" | sed -E 's/.*"diagnosis": *"([^"]*)".*/\1/')
if [ -n "$FIXED" ]; then
  echo 0 > "$GUARD/watchdog-state"     # reset strike counter
  alert 4 "JobPilot healer: fixed" "$DIAG"
else
  alert 5 "JobPilot healer: NOT fixed - human needed" "$DIAG"
fi
say "done fixed=${FIXED:-false}"

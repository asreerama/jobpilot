#!/bin/zsh
# JobPilot watchdog. INSTALLED TO ~/.local/jobpilot-guard/ (internal disk) on
# purpose: if the pipeline lives on an external volume, everything on that
# volume (supervisor, its wrapper, its plist targets) becomes unrunnable the
# moment it unmounts, and only a guard outside the volume can still detect
# that and alert. Canonical copy lives in pipeline/guard/; `guard/install.sh`
# syncs it over and substitutes the real ROOT below.
# Runs every 10 min from launchd. No shared code with jobpilot/ - a bug there
# must not take this down too.
GUARD="$HOME/.local/jobpilot-guard"
ROOT="${JOBPILOT_ROOT:-$HOME/jobpilot}"   # install.sh rewrites this line
HB="$ROOT/logs/supervisor-heartbeat"
LOG="$GUARD/watchdog.log"
STATE="$GUARD/watchdog-state"      # consecutive-intervention counter
LABEL="com.jobpilot.supervisor"
NOW=$(date '+%F %T')

say() { echo "[$NOW] $*" >> "$LOG"; }

alert() {  # alert <priority> <title> <body>
  local pri="$1" title="$2" body="$3"
  if [ -f "$HOME/.config/jobpilot/notify.sh" ]; then
    . "$HOME/.config/jobpilot/notify.sh"
    jp_notify "$title" "$body" "$pri" eyes || say "ALERT-FAIL $title"
  else
    say "ALERT-NOCHAN $title: $body"
  fi
}

interventions() { cat "$STATE" 2>/dev/null || echo 0; }
set_interventions() { echo "$1" > "$STATE"; }

# ---- 1. Is the pipeline directory reachable at all? -----------------------
if [ ! -d "$ROOT" ]; then
  say "pipeline root missing: $ROOT"
  alert 5 "JobPilot: pipeline unreachable" \
    "$ROOT is unmounted or unreadable. Nothing can run until it is back."
  exit 0            # nothing mechanical to fix from here
fi

# ---- 2. Disk space (catastrophic tier only) ------------------------------
PCT=$(df "$ROOT" | awk 'NR==2 {gsub("%","",$5); print $5}')
if [ "${PCT:-0}" -ge 95 ]; then
  alert 5 "JobPilot: disk ${PCT}% full" "Writes will start failing. Clean up now."
fi

# ---- 3. Daily DB safety snapshot (WAL-safe, off-volume copy) -------------
TODAY=$(date '+%F')
SNAP="$GUARD/backups/jobs-$TODAY.db"
if [ ! -f "$SNAP" ] && command -v sqlite3 >/dev/null; then
  sqlite3 "file:$ROOT/jobs.db?mode=ro" "VACUUM INTO '$SNAP.tmp'" 2>>"$LOG" \
    && mv "$SNAP.tmp" "$SNAP" \
    && cp "$ROOT/applications.csv" "$GUARD/backups/applications-$TODAY.csv" 2>>"$LOG" \
    && say "backup ok $SNAP"
  ls -t "$GUARD"/backups/jobs-*.db 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null
  ls -t "$GUARD"/backups/applications-*.csv 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null
fi

# ---- 4. Heartbeat freshness and state ------------------------------------
if [ ! -f "$HB" ]; then
  say "no heartbeat file"
  HB_AGE=99999
  HB_STATE="MISSING"
else
  HB_TS=$(stat -f %m "$HB")
  HB_AGE=$(( $(date +%s) - HB_TS ))
  HB_STATE=$(sed -E 's/.*state=([A-Z_]+).*/\1/' "$HB")
fi

# Holds are healthy: never intervene during them, only note.
case "$HB_STATE" in
  BACKOFF|CAP_HOLD|STOPPING|RELOADING)
    say "state=$HB_STATE age=${HB_AGE}s - healthy hold"
    set_interventions 0
    exit 0 ;;
esac

FRESH_LIMIT=300      # supervisor writes every 60s; 5 min stale = hung or dead
STALL_LIMIT=5400     # no terminal outcome in 90 min while work available

needs_kick=0
reason=""
if [ "$HB_AGE" -gt "$FRESH_LIMIT" ]; then
  needs_kick=1; reason="heartbeat ${HB_AGE}s stale (state=$HB_STATE)"
else
  # Heartbeat fresh: is anything actually FINISHING? Any terminal outcome
  # counts as progress, not just 'applied'.
  APPLYABLE=$(sed -E 's/.*applyable=([0-9]+).*/\1/' "$HB")
  LASTT=$(sed -E 's/.*last_terminal=([^ ]+).*/\1/' "$HB")
  if [ "${APPLYABLE:-0}" -gt 0 ] && [ "$LASTT" != "none" ]; then
    LAST_EPOCH=$(date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "$LASTT" +%s 2>/dev/null || echo 0)
    if [ "$LAST_EPOCH" -gt 0 ] && [ $(( $(date +%s) - LAST_EPOCH )) -gt "$STALL_LIMIT" ]; then
      needs_kick=1; reason="no terminal outcome in >90min, applyable=$APPLYABLE"
    fi
  fi
fi

if [ "$needs_kick" -eq 0 ]; then
  say "ok state=$HB_STATE age=${HB_AGE}s"
  set_interventions 0
  exit 0
fi

# ---- 5. Intervene: graceful TERM first, then kickstart -------------------
N=$(interventions)
N=$((N + 1))
set_interventions "$N"
say "intervention #$N: $reason"

SUP_PID=$(pgrep -f "bin/supervisor.py" | head -1)
if [ -n "$SUP_PID" ]; then
  kill "$SUP_PID" 2>/dev/null      # SIGTERM -> supervisor's graceful handler
  sleep 10
fi
launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>>"$LOG"
alert 4 "JobPilot watchdog restarted supervisor" "#$N: $reason"

# ---- 6. Two strikes without recovery -> healer ---------------------------
if [ "$N" -ge 2 ]; then
  say "two interventions without recovery -> healer"
  /bin/zsh "$GUARD/healer.sh" >> "$GUARD/healer-invoke.log" 2>&1 &
fi
exit 0

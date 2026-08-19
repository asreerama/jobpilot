#!/bin/zsh
# Long-running apply supervisor, kept alive by launchd.
# Mirrors apply.sh: same reachability check, same notify hooks. launchd
# captures stdout/stderr to logs/supervisor.log (see the plist template).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$HOME/.config/jobpilot/notify.sh" ]; then
  . "$HOME/.config/jobpilot/notify.sh"
else
  jp_notify() { :; }
fi

trap 'echo "[supervisor.sh] terminated $(date "+%F %T")"; exit 143' TERM INT

if [ ! -d "$ROOT" ]; then
  echo "[supervisor.sh] ROOT not accessible: $(ls -d "$ROOT" 2>&1)"
  jp_notify "⛔️ JobPilot supervisor — no disk" \
    "$(date '+%H:%M'): cannot read the pipeline directory. Volume unmounted, or /bin/zsh lost Full Disk Access." 5 floppy_disk
  sleep 300     # let launchd's KeepAlive retry rather than spin
  exit 0
fi

export PATH="/usr/bin:/bin:/usr/sbin:/opt/homebrew/bin:$HOME/.local/bin:$PATH"
cd "$ROOT" || { echo "[supervisor.sh] cd failed"; exit 1; }
echo "=== supervisor start $(date '+%F %T') ==="
"$ROOT/.venv/bin/python" "$ROOT/bin/supervisor.py" \
  --workers "${JOBPILOT_WORKERS:-6}" --batch "${JOBPILOT_BATCH:-5}"
rc=$?
echo "=== supervisor exit rc=$rc $(date '+%F %T') ==="
if [ $rc -ne 0 ] && [ $rc -ne 143 ]; then
  jp_notify "⛔️ JobPilot supervisor crashed" \
    "python exited $rc at $(date '+%H:%M'). launchd will restart it.
Log: logs/supervisor.log" 5 rotating_light
fi
exit $rc

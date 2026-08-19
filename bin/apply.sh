#!/bin/zsh
# Apply batch, invoked by launchd a few times a day.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$HOME/Library/Logs/jobpilot-apply.log"
if [ -f "$HOME/.config/jobpilot/notify.sh" ]; then
  . "$HOME/.config/jobpilot/notify.sh"
else
  jp_notify() { :; }
fi

echo "[apply.sh] start $(date '+%F %T') user=$(whoami)"

# launchd kills the run (system sleep, shutdown, force-unload).
trap '
  jp_notify "⛔️ JobPilot apply killed" \
    "Terminated mid-run at $(date "+%H:%M"). Jobs left in the applying state need a manual requeue.
Log: ~/Library/Logs/jobpilot-apply.log" 5 rotating_light
  exit 143
' TERM INT

if [ ! -d "$ROOT" ]; then
  echo "[apply.sh] ROOT not accessible: $(ls -d "$ROOT" 2>&1)"
  jp_notify "⛔️ JobPilot skipped: no disk" \
    "$(date '+%H:%M') run could not read the pipeline directory. Either the volume is unmounted, or /bin/zsh lost Full Disk Access." 5 floppy_disk
  exit 0
fi

export PATH="/usr/bin:/bin:/usr/sbin:/opt/homebrew/bin:$HOME/.local/bin:$PATH"
echo "[apply.sh] caps: day=${JOBPILOT_MAX_PER_DAY:-config} gap=${JOBPILOT_GAP_S:-config}"
cd "$ROOT" || { echo "[apply.sh] cd failed"; exit 1; }
echo "=== apply $(date '+%Y-%m-%d %H:%M:%S') ==="
"$ROOT/.venv/bin/python" -m jobpilot.parallel
rc=$?
# Nothing here uses `set -e`, so python failing would otherwise leave the
# script exiting 0 and the whole window passing in silence. This is the case
# the in-python hooks cannot report: they never ran.
if [ $rc -ne 0 ]; then
  jp_notify "⛔️ JobPilot apply crashed" \
    "python exited $rc at $(date '+%H:%M'). Nothing applied this window.
Log: ~/Library/Logs/jobpilot-apply.log" 5 rotating_light
fi
echo "=== done $(date '+%Y-%m-%d %H:%M:%S') rc=$rc ==="

#!/bin/zsh
# Discover + match, invoked by launchd every 3h. Safe to run manually.
# Runs 8x a day, so it only notifies on failure. Silence here means healthy.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$HOME/.config/jobpilot/notify.sh" ]; then
  . "$HOME/.config/jobpilot/notify.sh"
else
  jp_notify() { :; }
fi
[ -d "$ROOT" ] || exit 0   # volume not mounted; apply.sh raises that alarm
export PATH="/usr/bin:/bin:/usr/sbin:/opt/homebrew/bin:$HOME/.local/bin:$PATH"
cd "$ROOT" || exit 1
LOCKDIR="$ROOT/logs/cycle.shlock"
mkdir -p "$ROOT/logs"
if ! mkdir "$LOCKDIR" 2>/dev/null; then exit 0; fi
trap 'rmdir "$LOCKDIR"' EXIT
echo "=== cycle $(date '+%Y-%m-%d %H:%M:%S') ==="
if ! "$ROOT/.venv/bin/python" -m jobpilot.discover; then
  jp_notify "⚠️ JobPilot discovery failed" \
    "$(date '+%H:%M') board sweep errored out. The apply queue will run dry if this keeps happening.
Log: ~/Library/Logs/jobpilot-cycle.log" 4 warning
fi
if ! "$ROOT/.venv/bin/python" -m jobpilot.match; then
  jp_notify "⚠️ JobPilot scoring failed" \
    "$(date '+%H:%M') match/score errored out. New jobs are sitting unscored.
Log: ~/Library/Logs/jobpilot-cycle.log" 4 warning
fi
echo "=== done $(date '+%Y-%m-%d %H:%M:%S') ==="

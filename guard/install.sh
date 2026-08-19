#!/bin/zsh
# Install/refresh the off-volume guard (watchdog + healer) from the canonical
# copies in pipeline/guard/. Idempotent; safe to re-run after edits.
#
# The installed copies live on the internal disk, so they cannot resolve the
# pipeline root relatively. This script takes ROOT from its own location and
# substitutes it into them.
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SRC/.." && pwd)"
GUARD="$HOME/.local/jobpilot-guard"
mkdir -p "$GUARD/backups" "$GUARD/snapshots" "$HOME/Library/LaunchAgents"

for f in watchdog.sh healer.sh; do
  sed "s|^ROOT=\".*\".*$|ROOT=\"$ROOT\"|" "$SRC/$f" > "$GUARD/$f"
  chmod +x "$GUARD/$f"
done

sed "s|{{HOME}}|$HOME|g" "$SRC/com.jobpilot.watchdog.plist.template" \
  > "$HOME/Library/LaunchAgents/com.jobpilot.watchdog.plist"
launchctl bootout "gui/$(id -u)/com.jobpilot.watchdog" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.jobpilot.watchdog.plist"
echo "guard installed for ROOT=$ROOT; watchdog scheduled every 10 min"

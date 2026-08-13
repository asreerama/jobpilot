# Shell-side push for the launchd wrappers. Lives in $HOME and depends on
# nothing but curl, so it still fires when the data volume is unreadable.
# usage: jp_notify "title" "body" [priority 1-5] [tag]
jp_notify() {
  [ -f "$HOME/.config/jobpilot/notify.env" ] || return 0
  . "$HOME/.config/jobpilot/notify.env"
  _jp_esc() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
      | awk 'BEGIN{ORS=""} NR>1{print "\\n"} {print}'
  }
  local t="$(_jp_esc "$1")" b="$(_jp_esc "$2")" p="${3:-3}" g="${4:-gear}"
  if [ "${NOTIFY_BACKEND:-ntfy}" = "pushcut" ]; then
    [ -n "$PUSHCUT_KEY" ] || return 0
    local ts=false; [ "$p" -ge 4 ] && ts=true
    curl -s -m 5 -H "Content-Type: application/json" -H "API-Key: $PUSHCUT_KEY" \
      -d "{\"title\":\"$t\",\"text\":\"$b\",\"isTimeSensitive\":$ts}" \
      "https://api.pushcut.io/v1/notifications/${PUSHCUT_NOTIFICATION:-JobPilot}" \
      >/dev/null 2>&1
  else
    [ -n "$NTFY_TOPIC" ] || return 0
    curl -s -m 5 -H "Content-Type: application/json" \
      -d "{\"topic\":\"$NTFY_TOPIC\",\"title\":\"$t\",\"message\":\"$b\",\"priority\":$p,\"tags\":[\"$g\"]}" \
      "${NTFY_SERVER:-https://ntfy.sh}" >/dev/null 2>&1
  fi
}

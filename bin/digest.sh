#!/bin/zsh
# Morning digest, invoked by launchd daily.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -d "$ROOT" ] || exit 0
export PATH="/usr/bin:/bin:/usr/sbin:/opt/homebrew/bin:$HOME/.local/bin:$PATH"
cd "$ROOT" || exit 1
"$ROOT/.venv/bin/python" -m jobpilot.digest

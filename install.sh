#!/bin/zsh
# JobPilot bootstrap: venv, deps, working dirs, example configs.
# Idempotent; run it again any time.
set -e
cd "$(dirname "$0")"

echo "== python venv =="
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo "== working directories =="
mkdir -p logs logs/workers out/applications secrets resume/out resume/tailor
mkdir -p workers/profiles

echo "== per-worker browser configs =="
# The supervisor fleet needs one Playwright MCP config per worker slot, each
# pinned to its own Chrome profile directory so workers cannot fight over one.
for n in 1 2 3 4 5 6; do
  cat > "workers/pw-$n.json" <<JSON
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": [
        "@playwright/mcp@latest",
        "--user-data-dir",
        "$PWD/workers/profiles/chrome-$n"
      ]
    }
  }
}
JSON
done

echo "== config files =="
[ -f config.yaml ]        || { cp config.example.yaml config.yaml; echo "  created config.yaml (EDIT IT)"; }
[ -f profile.md ]         || { cp profile.example.md profile.md;   echo "  created profile.md (EDIT IT)"; }
[ -f resume/resume.json ] || { cp resume/resume.example.json resume/resume.json; echo "  created resume/resume.json (EDIT IT)"; }

echo
echo "Done. Next steps (docs/SETUP.md has the full walkthrough):"
echo "  1. Edit config.yaml, profile.md, resume/resume.json"
echo "  2. .venv/bin/python -m jobpilot.seed --quick"
echo "  3. .venv/bin/python -m jobpilot.discover && .venv/bin/python -m jobpilot.match"
echo "  4. .venv/bin/python -m jobpilot.applier --dry-run"
echo
echo "Or let Claude Code walk you through it: /jobpilot:setup"

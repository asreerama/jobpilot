---
description: Guided first-time JobPilot setup - install directory, config, profile, resume, notifications, schedule
---

You are running JobPilot's guided first-time setup. Walk the user through it
interactively, one stage at a time. Ask before each stage, show what you
wrote, and let them correct it. Never invent facts about the user; everything
personal comes from their answers.

## Stage 0: prerequisites check

Check and report each of these, with the fix if missing:

- macOS (launchd scheduling and the digest notification are macOS-only; on
  Linux the pipeline runs but they must port the schedule to cron/systemd).
- `python3 --version` (3.10+)
- `claude --version` (Claude Code CLI, logged in to a subscription; the
  scorer and applier run `claude -p` and must never use an API key)
- A Playwright MCP server available to Claude Code (`claude mcp list`), or
  note that apply runs use `pw-isolated.json` bundled in the repo via
  `--mcp-config`, which needs `npx` (Node).
- `git`
- Optional, for the LaTeX resume renderer: `pdflatex` and `pdftoppm`
  (`brew install --cask basictex`, `brew install poppler`).

## Stage 1: install directory

Ask where to install the pipeline (default `~/jobpilot`). Then:

- If this command is running from a git checkout of jobpilot (the repo root
  contains `config.example.yaml` and `jobpilot/`), use that checkout as the
  install directory instead of cloning again, if the user agrees.
- Otherwise `git clone https://github.com/asreerama/jobpilot` into the chosen
  directory.

Then create the venv:

```bash
cd <install-dir>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p logs out/applications secrets
```

## Stage 2: config.yaml

Copy `config.example.yaml` to `config.yaml`. Interview the user for each
CHANGE ME section and write their answers in:

- name, email, location
- the one-line candidate summary for the scorer (role, years, domains, visa
  status, location constraints)
- target job titles -> title_include_patterns / title_exclude_patterns
- metro area or remote preference -> bay_area_or_remote + hints (rename
  mentally; the key works for any metro)
- max required years of experience, posting age
- whether they need visa sponsorship (if not, set sponsorship_blockers to [])
- scoring.calibration: their seniority sweet spot and location rules
- apply caps (defaults are sane: 40/day, 10/batch, 5 min gaps)

## Stage 3: profile.md

Copy `profile.example.md` to `profile.md` and rewrite it with the user, section
by section. This file is injected verbatim into every apply run, so it must be
complete: identity line, work-authorization block (pick the right one), fixed
answers (including their explicit choices on demographic questions and salary),
free-text answer facts, and any special instructions. Tell the user this file
is also where they add special instructions later - things like "never apply
to competitors of X", writing-style rules, or dropdown quirks for their degree.

## Stage 4: resume data

Copy `resume/resume.example.json` to `resume/resume.json`. Offer two paths:

- If the user has a resume PDF/text, read it and build resume.json from it
  (facts only, confirm with them; give every bullet a short stable id).
- Otherwise interview them role by role.

Then test-render: `cd resume && python3 render_tex.py` (or `render.py` if no
LaTeX) and show them the output PNG via `pdftoppm`.

## Stage 5: seed the company database

```bash
.venv/bin/python -m jobpilot.seed --quick   # fast: verified list only
# or without --quick to also probe ~6,000 extra slugs (a few minutes)
```

If they have a CSV of past applications, put it at `applications.csv`
(header: `date_applied,company,role,outcome,ats,url,application_id`) first so
seeding registers those companies and the deduper knows their history.

## Stage 6: phone notifications (optional, recommended)

```bash
mkdir -p ~/.config/jobpilot
cp notify/notify.sh ~/.config/jobpilot/notify.sh
cp notify/notify.env.example ~/.config/jobpilot/notify.env
```

Generate a long random ntfy topic (`openssl rand -hex 12` prefixed with a
word), write it into `~/.config/jobpilot/notify.env`, and tell the user to
subscribe to that topic in the ntfy iOS/Android app. Test with:

```bash
.venv/bin/python -m jobpilot.notify "JobPilot" "setup test" 3 tada
```

## Stage 7: first supervised run

```bash
.venv/bin/python -m jobpilot.discover      # takes a while on first run
.venv/bin/python -m jobpilot.match
.venv/bin/python -m jobpilot status
.venv/bin/python -m jobpilot list queued
.venv/bin/python -m jobpilot.applier --dry-run
```

Show the user the queue and the dry-run prompt. Tell them to run their first
real apply batch while watching:
`.venv/bin/python -m jobpilot.applier`, then review the record in
`out/applications/`.

## Stage 8: going hands-off (launchd)

Only after the user has reviewed at least one supervised application. Render
the templates and load them:

```bash
mkdir -p ~/Library/LaunchAgents
for t in launchd/*.plist.template; do
  name=$(basename "$t" .template)
  sed "s|{{ROOT}}|$PWD|g" "$t" > ~/Library/LaunchAgents/$name
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$name
done
```

Warn about the hands-off requirements (also in README "Going hands-off"):
machine awake at run times, Full Disk Access for /bin/zsh if the repo lives on
an external volume, Chrome profile logins, and Claude plan usage limits.

Finally write the install path to `~/.config/jobpilot/root` so
`/jobpilot:status` can find it:

```bash
echo "$PWD" > ~/.config/jobpilot/root
```

Finish by summarizing what is configured, what is scheduled, and where the
review records and daily digest land.

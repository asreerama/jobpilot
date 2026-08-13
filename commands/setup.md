---
description: Guided first-time JobPilot setup - install directory, config, profile, resume, notifications, schedule
---

You are running JobPilot's guided first-time setup. Walk the user through it
interactively, one stage at a time. Ask before each stage, show what you
wrote, and let them correct it. Never invent facts about the user; everything
personal comes from their answers.

Tone for the whole setup: direct and concrete. Get to the point. No
marketing language, no hedging, no "great question!". When a stage has a
tradeoff (plaintext password, inbox access, ToS risk), state it in one plain
sentence and move on. The user is trusting this thing to submit job
applications with their name on it; they need to understand it, fast.

## Stage -1: tell the user what they are getting

Before checking anything, give this briefing in your own words, compressed.
Do not skip it and do not pad it:

**What it does.** Finds jobs by polling ~2,000 company ATS boards
(Greenhouse, Lever, Ashby, Workable, Recruitee, SmartRecruiters, Workday)
plus Indeed/LinkedIn sweeps every 3 hours. Hard-filters by title/location/
age/sponsorship, scores each survivor 0-100 with Claude against their
criteria. Score >= 70 goes to a queue. Five times a day, a Claude agent
takes the queue and applies for real: tailors a one-page resume per job,
fills the form in a real browser, screenshots it, submits, and logs it. A
daily digest reports everything.

**What it costs.** Runs on their Claude subscription via the CLI (scoring on
Haiku, applying on Sonnet). No API key, ever. Heavy applying eats plan
quota; the pipeline backs off automatically when the plan window is
exhausted.

**What it will NOT do.** Solve captchas, pass login walls it cannot pass,
LinkedIn Easy Apply, or invent resume facts. Those jobs park in a
needs_human queue for the user.

**How they use it day to day.** Read the morning digest, clear the
needs_human queue, spot-check review records. `/jobpilot:status` or
`python -m jobpilot status` any time. `promote`/`skip` to override the
queue.

**Where things live** (show this table):

| Path | What is in it |
|---|---|
| `config.yaml` | what to search for; scoring rules |
| `profile.md` | who is applying; every fixed form answer; special instructions |
| `resume/resume.json` | resume data (bullets with ids) |
| `resume/out/` | every rendered resume PDF, including the tailored one per job |
| `resume/tailor/` | the per-job tailor overlays (what changed and why) |
| `out/applications/` | review record per application: every field + answer + pre-submit screenshot |
| `out/digest-YYYY-MM-DD.md` | the daily digest |
| `jobs.db` | SQLite: every job seen, scored, applied |
| `applications.csv` | flat log of everything submitted; the duplicate guard |
| `logs/`, `~/Library/Logs/jobpilot-*.log` | run logs |
| `secrets/workday.json` | Workday email + standard password (created in stage 6) |

**The honest caveats, up front:** every answer on every form comes from
profile.md, which they write; the resume tailor cannot invent experience;
automated submission may violate some sites' terms of service, and the
defaults (40/day cap, minutes between submissions, no Easy Apply) are
deliberately conservative; review records exist so they can audit every
application after the fact, and they should actually read them, especially
the first week.

Then ask if they want to proceed.

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

## Stage 2: config.yaml - the filtering and scoring criteria

First explain, in two sentences, how a job gets judged: layer 1 is hard
filters in `criteria.*` (title regexes, location, posting age, required
years, sponsorship blockers) that kill jobs before any LLM sees them;
layer 2 is a Claude score 0-100 driven by `profile.summary` +
`scoring.calibration`, where >= 70 (`apply_threshold`) auto-queues and
>= 50 (`review_threshold`) shows in the digest for manual promotion.

Tell them where to change it later: everything in this stage is
`config.yaml`, plain YAML, edit any time; changes apply from the next
discover/match cycle, no restart needed. If good jobs are being filtered
out, loosen `criteria.*`; if bad jobs are getting queued, sharpen
`scoring.calibration` or raise `apply_threshold`.

Copy `config.example.yaml` to `config.yaml`. Then interview the user ONE
QUESTION AT A TIME - ask, wait for the answer, write it into config.yaml,
show what you wrote, then ask the next. Do not dump one giant questionnaire.
The sequence:

1. Name, email, location.
2. The one-line candidate summary for the scorer (role, years, domains,
   visa status, location constraints).
3. Target job titles -> build title_include_patterns with them; read the
   regexes back in plain English.
4. Titles to always exclude (too senior, wrong function) ->
   title_exclude_patterns.
5. Location: which metro, or remote-only, or anywhere in the US ->
   bay_area_or_remote + hints (the key name says Bay Area; the hints are
   just regexes, put their metro's cities in).
6. Max required years of experience they want to see; max posting age.
7. Do they need visa sponsorship? If no, set sponsorship_blockers to [].
8. Salary floor, if any (min_salary_ceiling; 0 disables).
9. Companies to never apply to (current employer, blocklist).
10. scoring.calibration: their seniority sweet spot, adjacent titles they
    accept, location rules, domains that deserve a bump or a penalty.
    Draft it from their earlier answers, show it, let them edit.
11. Apply caps - defaults are sane (40/day, 10/batch, 5 min gaps); ask only
    whether they want them lower to start.

## Stage 3: profile.md

Copy `profile.example.md` to `profile.md` and rewrite it with the user, section
by section. This file is injected verbatim into every apply run, so it must be
complete: identity line, work-authorization block (pick the right one), fixed
answers (including their explicit choices on demographic questions and salary),
free-text answer facts, and any special instructions. Tell the user this file
is also where they add special instructions later - things like "never apply
to competitors of X" or dropdown quirks for their degree.

Point out the **writing rules** block in the free-text section explicitly:
the template ships defaults that keep everything written in their voice
(application answers, resume bullets) reading like a person, not a model -
no em-dashes, no strawman contrast, no explainer clauses, the delete test.
Recommend keeping them as-is, and tell the user they are plain text in
profile.md: edit, extend, or delete them like anything else in the file. If
they change the em-dash or contrast rules, mention that `_BANNED` in
`resume/render.py` mechanically enforces those two on resume text and should
be edited to match.

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

## Stage 6: Workday credentials (optional)

Explain how Workday applications work before asking anything: every Workday
company (tenant) requires its own account. The apply agent signs in with
Google SSO when the tenant offers it and the browser profile has a Google
session; otherwise it signs in or creates the account using ONE email + ONE
standard password stored in `secrets/workday.json`:

```bash
cp secrets/workday.json.example secrets/workday.json
```

Tell the user plainly:

- Pick a password used for NOTHING else. It is stored in plain text on disk
  (the file is gitignored and never leaves the machine) and will be typed
  into many third-party Workday tenants.
- Until this file exists, Workday jobs are not attempted; they park in the
  needs_human queue instead. Skipping this stage is fine.
- Tenants that email a verification code stop at needs_human, UNLESS a
  Gmail/email MCP is connected (next paragraph).

**Recommend connecting a Gmail MCP** for the email address used on
applications. With it, the apply agent reads verification codes and
confirmation links itself (search recent messages, extract the code), so
account creation on Workday and similar ATSes completes without the user.
Without it, every emailed code parks the job in needs_human. Setup: `/mcp`
in Claude Code -> connect the Gmail connector and authorize the account
(one-time OAuth). Note the tradeoff honestly: the agent gets read access to
the inbox; a dedicated job-search Gmail address keeps that scoped and also
keeps recruiter mail in one place.

## Stage 7: phone notifications (optional, recommended)

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

## Stage 8: first supervised run

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

## Stage 9: going hands-off (launchd)

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

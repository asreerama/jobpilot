# JobPilot

A 24/7 job application pipeline that runs on your Mac. It discovers jobs
directly from company ATS boards, filters and scores them against your
criteria with Claude, applies with a per-job tailored resume through a real
browser, and sends you a daily digest plus phone notifications. Everything
LLM runs through the Claude Code CLI on a Claude subscription. No API keys.

```
discover (every 3h)                  match (same run)              apply (5 windows/day)
  ATS boards: greenhouse, lever,  ->  hard filters:             ->  claude -p per job:
  ashby, workable, recruitee,         title, location, salary,      reads your profile.md,
  smartrecruiters, workday CXS        age, sponsorship regex,       tailors the resume,
  JobSpy: indeed, linkedin            history dedupe                fills the form with
  SimplifyJobs listings.json      ->  LLM fit score 0-100           Playwright, screenshots,
                                      >=70 -> apply queue           submits, writes a
                                                                    review record
```

Every application writes a review record before submitting: every field, the
exact answer entered, free text in full, and a full-page screenshot. You
audit what the machine did, on your schedule.

## What you need

| Requirement | Why |
|---|---|
| macOS | scheduling is launchd; the digest uses osascript. The Python pipeline itself is portable, but you would port the schedule to cron/systemd yourself |
| Python 3.10+ | the pipeline |
| [Claude Code](https://claude.com/claude-code) CLI, logged in to a subscription | scoring (`claude -p` with Haiku) and applying (Sonnet). The pipeline strips `ANTHROPIC_API_KEY` from the environment on purpose: high-volume applying on API pricing gets expensive fast |
| Node (`npx`) | apply runs launch an isolated [Playwright MCP](https://github.com/microsoft/playwright-mcp) browser via `pw-isolated.json` |
| BasicTeX + poppler (optional) | the ATS-safe LaTeX resume renderer; there is a Chrome-based fallback renderer |
| [ntfy](https://ntfy.sh) app on your phone (optional) | push notifications for every apply window and failure |

## Install

### Option A: Claude Code plugin with guided setup (recommended)

```
/plugin marketplace add asreerama/jobpilot
/plugin install jobpilot@jobpilot
/jobpilot:setup
```

`/jobpilot:setup` walks you through everything interactively: prerequisites,
install directory, your search criteria, your application profile, your
resume data, seeding ~2,000 company boards, notifications, a first
supervised application, and only then the hands-off schedule.

### Option B: manual

```bash
git clone https://github.com/asreerama/jobpilot ~/jobpilot
cd ~/jobpilot
./install.sh
```

Then follow [docs/SETUP.md](docs/SETUP.md). The short version: edit
`config.yaml` (search criteria, scoring calibration), `profile.md` (your
identity and fixed form answers), and `resume/resume.json` (your resume
data), then seed and run.

## The three files that make it yours

All three are gitignored. Nothing personal lives in the code.

1. **`config.yaml`** (from `config.example.yaml`): what to search for. Title
   include/exclude regexes, location gate, posting age, salary floor,
   sponsorship blockers, JobSpy searches, apply caps, and
   `scoring.calibration`, the free-text rules the LLM scorer follows.
2. **`profile.md`** (from `profile.example.md`): who is applying. Identity,
   work authorization, fixed answers for every recurring form question
   (relocation, demographics, salary), the facts free-text answers may draw
   on, and your special instructions. This file is injected verbatim into
   every apply run, so the agent follows exactly what you wrote.
3. **`resume/resume.json`** (from `resume/resume.example.json`): your resume
   as structured data with stable bullet ids. Each application gets a small
   tailor overlay (drop/reorder/rewrite bullets) rendered to a single-page,
   single-column, ATS-parseable PDF.

### Adding special instructions

Put them in `profile.md` under "Special instructions". Examples: never apply
to competitors of your current employer, always fill the optional portfolio
field, which dropdown option to pick when your degree name is missing,
writing-style rules for anything in your voice. The apply agent receives the
whole file every run. Scoring-side instructions ("penalize agencies",
"boost climate companies") go in `scoring.calibration` in `config.yaml`.

## Daily use

```bash
.venv/bin/python -m jobpilot status            # pipeline overview
.venv/bin/python -m jobpilot list queued       # what will be applied to next
.venv/bin/python -m jobpilot list needs_human  # blocked: captcha/login/etc
.venv/bin/python -m jobpilot show <id>         # full record incl. JD snippet
.venv/bin/python -m jobpilot promote <id>      # push a near-miss into the queue
.venv/bin/python -m jobpilot skip <id> reason  # remove from queue
.venv/bin/python -m jobpilot add-company <slug> <ats>  # track a company board
.venv/bin/python -m jobpilot.discover          # manual discovery run
.venv/bin/python -m jobpilot.match             # manual filter+score run
.venv/bin/python -m jobpilot.applier --dry-run # see what it would do
.venv/bin/python -m jobpilot.applier           # apply serially, gaps between jobs
.venv/bin/python -m jobpilot.parallel          # apply with 4 concurrent workers
.venv/bin/python -m jobpilot.digest            # generate the digest now
.venv/bin/python -m jobpilot.seed              # re-seed companies
```

With the plugin installed, `/jobpilot:status` gives you the same overview
inside Claude Code.

Review records land in `out/applications/<id>-<company>.{md,png}`. The daily
digest lands in `out/digest-YYYY-MM-DD.md`.

## Going hands-off

The pipeline is designed to run unattended on a Mac that stays on (a Mac mini
is ideal). Run `/jobpilot:setup` stage 8, or render the launchd templates
yourself (see [docs/SETUP.md](docs/SETUP.md#8-schedule-it-launchd)). The
schedule: discover+match every 3 hours, five apply windows a day, digest at
08:05.

Hands-off operation has real requirements. Check every one:

- **The machine must be awake** at run times. `sudo pmset repeat wakeorpoweron MTWRFSU 09:35:00` or Amphetamine/caffeinate. launchd skips runs that fall in sleep.
- **Full Disk Access for `/bin/zsh`** (System Settings > Privacy & Security) if the repo lives on an external volume, or launchd jobs cannot read it. This failure is silent, and it is exactly what the ntfy alerts exist to catch.
- **Claude CLI logged in** and the plan's usage window not exhausted. The pipeline detects usage-limit responses, requeues the job, and backs off for 90 minutes on its own; lower `apply.max_per_day` if you hit limits daily.
- **Browser logins**: a Google session in the Playwright browser profile makes Workday tenants with Google SSO sign in without passwords. Workday tenants without it use `secrets/workday.json` (one standard password you choose, used for every tenant account it creates).
- **Notifications configured** (stage 6 of setup), or you will not hear about failures. `cycle.sh` is silent when healthy and loud when broken by design.
- **Check the needs_human queue** in the morning digest. Captchas, login walls, and email-verification codes park there and wait for you.

Pause everything: `launchctl bootout gui/$(id -u)/com.jobpilot.apply` (same
for `cycle`/`digest`). Resume with `launchctl bootstrap gui/$(id -u)
~/Library/LaunchAgents/com.jobpilot.apply.plist`.

## Behavior worth knowing

- **LinkedIn is never auto-applied through Easy Apply** by default policy in
  the apply prompt: LinkedIn-sourced jobs are applied on the employer's own
  ATS, found via web search. Easy Apply bots get accounts restricted.
- **Submissions are paced**: serial mode keeps minutes between submissions;
  caps live in `config.yaml` (`max_per_day`, `max_per_batch`,
  `min_gap_minutes`). A real Chrome, human pacing, and a residential IP are
  what pass ATS fraud detection in 2026; do not crank the caps.
- **The company table is self-maintaining**: JobSpy discoveries register new
  ATS boards, dead boards deactivate after repeated 404s.
- **Dedupe is layered**: canonical-URL uniqueness in SQLite, company+title
  pairs against your `applications.csv` history, and a re-check at apply
  time.
- **Job status lifecycle**: `discovered -> filtered_out |
  skipped_no_sponsorship | duplicate | scored | queued -> applying ->
  applied | needs_human | failed | expired`. `jobpilot list <status>` shows
  any bucket.

## Use responsibly

You are submitting real job applications with your name on them. Keep
`profile.md` truthful; the resume tailor is constrained to never invent
experience, and you should review `out/applications/` records regularly.
Automated submission may violate some sites' terms of service; the defaults
(low caps, human pacing, no Easy Apply, no captcha solving) are deliberately
conservative. What you do with the caps is on you.

## Repo layout

```
jobpilot/            the Python package (discover, filter, score, apply, digest, notify)
bin/                 launchd entrypoints (apply.sh, cycle.sh, digest.sh)
launchd/             plist templates, rendered by setup
seeds/               ~2,000 verified ATS board slugs to bootstrap discovery
skills/              Claude skills: job-applications, resume-tailoring
commands/            /jobpilot:setup and /jobpilot:status
resume/              resume renderers (LaTeX + Chrome) and your resume data
notify/              ntfy/Pushcut templates for ~/.config/jobpilot/
APPLY_PROTOCOL.md    the browser-driving protocol every apply run follows
config.example.yaml  search criteria template
profile.example.md   candidate profile template
```

## License

MIT. Built by [Aditya Sreerama](https://github.com/asreerama).

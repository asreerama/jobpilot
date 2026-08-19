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
| Gmail MCP connected in Claude Code (optional, recommended) | the apply agent fetches emailed verification codes itself during ATS account creation; without it those jobs park in needs_human |

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
.venv/bin/python bin/backfill_jd.py            # fetch JDs for needs_jd rows
```

With the plugin installed, `/jobpilot:status` gives you the same overview
inside Claude Code.

Review records land in `out/applications/<id>-<company>.{md,png}`. The daily
digest lands in `out/digest-YYYY-MM-DD.md`.

## Going hands-off

The pipeline is designed to run unattended on a Mac that stays on (a Mac mini
is ideal). Run `/jobpilot:setup` stage 9, or render the launchd templates
yourself (see [docs/SETUP.md](docs/SETUP.md#10-schedule-it-launchd)). The
schedule: discover+match every 3 hours, five apply windows a day, digest at
08:05.

Hands-off operation has real requirements. Check every one:

- **The machine must be awake** at run times. `sudo pmset repeat wakeorpoweron MTWRFSU 09:35:00` or Amphetamine/caffeinate. launchd skips runs that fall in sleep.
- **Full Disk Access for `/bin/zsh`** (System Settings > Privacy & Security) if the repo lives on an external volume, or launchd jobs cannot read it. This failure is silent, and it is exactly what the ntfy alerts exist to catch.
- **Claude CLI logged in** and the plan's usage window not exhausted. The pipeline detects usage-limit responses, requeues the job, and backs off for 90 minutes on its own; lower `apply.max_per_day` if you hit limits daily.
- **Browser logins**: a Google session in the Playwright browser profile makes Workday tenants with Google SSO sign in without passwords. Workday tenants without it use `secrets/workday.json` (setup stage 6: one standard password you choose, used for every tenant account it creates; use it for nothing else).
- **Notifications configured** (stage 7 of setup), or you will not hear about failures. `cycle.sh` is silent when healthy and loud when broken by design.
- **Check the needs_human queue** in the morning digest. Captchas, login walls, and email-verification codes park there and wait for you.

Pause everything: `launchctl bootout gui/$(id -u)/com.jobpilot.apply` (same
for `cycle`/`digest`/`supervisor`/`watchdog`). Resume with `launchctl
bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jobpilot.apply.plist`.

## Durability

Unattended for weeks means every layer needs something above it that notices
when it stops. There are three.

**The supervisor owns the fleet.** `bin/supervisor.sh` runs under launchd with
`KeepAlive`, and `bin/supervisor.py` loops every 60 seconds: reap dead workers
and respawn them (three crashes inside ten minutes parks that slot), recover
expired claims, run match when the queue goes dry, promote scored jobs at or
above `promote_floor` when match produced nothing, and write a heartbeat.
Workers are `bin/worker_apply.py` processes, each with its own Chrome profile
via `workers/pw-N.json`. When a watched source file changes on disk and still
compiles, the supervisor exits 0 and launchd relaunches it on the new code.

**The watchdog checks the supervisor**, every 10 minutes. `guard/watchdog.sh`
is installed to `~/.local/jobpilot-guard/` on the internal disk, off the
pipeline volume on purpose: if the pipeline lives on an external drive,
nothing on that drive can report the drive being gone. It reads
`logs/supervisor-heartbeat` and intervenes on two conditions: the heartbeat is
more than 5 minutes stale, or the heartbeat is fresh but nothing has reached a
terminal status in 90 minutes while applyable work is sitting in the queue.
Intervention is SIGTERM to the supervisor, then `launchctl kickstart`. The
same run takes a daily `VACUUM INTO` snapshot of `jobs.db` and
`applications.csv` to the internal disk, and alerts at 95% disk use.

**The healer is the last resort.** Two watchdog interventions without recovery
invoke `guard/healer.sh`, which hands the evidence (supervisor log, watchdog
log, heartbeat, launchd state, status counts, worker logs) to `claude -p`
under a narrow mandate: edit only `bin/`, `jobpilot/`, `config.yaml`; never
touch database rows, `applications.csv`, `secrets/`, or `out/`. The limits are
enforced in the script, not in the prompt: one healer at a time, two runs per
day, a 30 minute timeout, and a snapshot of `bin/` and `jobpilot/` taken
before Claude may edit anything. Verification is non-submitting: a fresh
`RUNNING` heartbeat plus a row entering `applying`. A real application is
never part of the test. If the healer cannot fix it, or diagnoses something
outside its mandate, you get a push notification saying a human is needed.

### Heartbeat states

`logs/supervisor-heartbeat` is rewritten atomically (temp file plus rename)
every cycle, carrying one state plus the counts behind it.

| State | Meaning |
|---|---|
| `RUNNING` | workers are up and there is applyable work |
| `MATCHING` | queue dry; match running synchronously over discovered jobs |
| `PROMOTING` | queue dry and nothing new matched; promoting scored jobs above the floor |
| `DRY` | nothing left that this fleet is allowed to apply to |
| `CAP_HOLD` | daily application cap reached |
| `BACKOFF` | Claude usage limit hit; the whole queue is paused |
| `RELOADING` | watched source changed; exiting for a clean launchd restart |
| `STOPPING` | SIGTERM received; workers left to finish their claims |

The watchdog treats `CAP_HOLD`, `BACKOFF`, `RELOADING` and `STOPPING` as
healthy holds and never intervenes during them. It keys off the state string,
never off raw counts.

### Claim fencing and lease recovery

Every claim stamps `claim_pid`, `claim_worker`, `claim_attempt` and
`lease_until` (45 minutes) onto the job row, taken with a conditional
`UPDATE ... WHERE status='queued'` so two workers cannot win the same job.
The supervisor requeues an expired lease only when the owning PID is also
dead; a slow worker is left alone until it finishes.

Ambiguous submissions are never retried. If the dead worker got far enough to
write its review record in `out/applications/`, the form may already have been
submitted, so the job goes to `needs_human` with a note instead of back into
the queue. `applied` is a terminal status at the database layer: `set_status`
refuses to overwrite it, and a worker whose result lands after its job was
already finished elsewhere drops the result rather than double-recording it.

### Installing the guard

```bash
zsh guard/install.sh
```

Copies `watchdog.sh` and `healer.sh` to `~/.local/jobpilot-guard/` with your
real pipeline path substituted in, renders the watchdog plist, and loads it.
Idempotent; re-run it after editing either script. The supervisor itself
installs like the other agents, from `launchd/com.jobpilot.supervisor.plist.template`.

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
bin/                 launchd entrypoints (apply.sh, cycle.sh, digest.sh, supervisor.sh),
                     the supervisor and its workers, and backfill_jd.py
guard/               off-volume watchdog + healer and their installer
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

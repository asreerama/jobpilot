# Manual setup

The plugin path (`/jobpilot:setup` inside Claude Code) automates this whole
page. Use this page if you prefer doing it by hand, or to understand what the
guided setup did.

## 1. Prerequisites

```bash
python3 --version        # 3.10+
claude --version         # Claude Code CLI, logged in (claude login)
node --version           # for the Playwright MCP browser
git --version
# optional, for the ATS-safe LaTeX resume renderer:
brew install --cask basictex
sudo tlmgr install collection-fontsrecommended enumitem titlesec microtype
brew install poppler
```

The scorer and applier shell out to `claude -p` on your subscription. The
pipeline strips `ANTHROPIC_API_KEY` from the environment before every call,
so it can never silently bill an API key.

## 2. Clone and install

```bash
git clone https://github.com/asreerama/jobpilot ~/jobpilot
cd ~/jobpilot
./install.sh
```

`install.sh` creates `.venv`, installs `requirements.txt`, creates
`logs/ out/applications/ secrets/`, and copies the three example files if the
real ones do not exist yet.

## 3. config.yaml: what to search for

Open `config.yaml` and edit every `CHANGE ME`:

- `profile.summary`: one paragraph about you for the scorer. Role, years,
  domains, standout work, visa status, location constraints.
- `criteria.title_include_patterns` / `title_exclude_patterns`: regexes over
  job titles. This is the cheapest, most effective filter; tune it first.
- `criteria.bay_area_or_remote` + `bay_area_hints`: the metro gate. The key
  name says Bay Area; the hints are just regexes, so put your own metro's
  cities there, or set it `false` to accept any US location.
- `criteria.sponsorship_blockers`: keep if you need visa sponsorship, set to
  `[]` if you do not.
- `discovery.jobspy.searches`: the Indeed/LinkedIn sweeps. Put your role and
  metro in.
- `scoring.calibration`: free-text scoring rules. Seniority sweet spot,
  adjacent titles you accept, location rules, domains that deserve a bump.
- `apply.*`: caps. Defaults: 40/day, 10/batch, 5-minute gaps, Sonnet.

If `claude` is not on launchd's PATH, set `scoring.claude_bin` and
`apply.claude_bin` to the absolute path (`which claude`).

## 4. profile.md: who is applying

```bash
cp profile.example.md profile.md
```

Rewrite every section. This file is pasted into every apply run verbatim; the
applying agent answers forms with exactly what it says. Cover: identity line
(name, city, email, phone, links, education), work-authorization block, fixed
answers (relocation, veteran status, demographics, salary policy, "how did
you hear"), the facts free-text answers may use, and special instructions.

Do not skip the demographic answers. If you leave them undecided the agent
has to improvise on a live form. Write down either your answers or an
explicit "select prefer-not-to-say everywhere".

## 5. resume/resume.json: your resume as data

```bash
cp resume/resume.example.json resume/resume.json
```

Fill in your real experience. Give every bullet a short stable id; tailor
overlays reference them. Then verify it renders to one page:

```bash
cd resume
export PATH="/Library/TeX/texbin:$PATH"
python3 render_tex.py                      # -> out/master-ats.pdf
pdftoppm -png -r 130 out/master-ats.pdf out/check   # look at out/check-1.png
pdftotext -layout out/master-ats.pdf - | head -30   # ATS parse order check
cd ..
```

No LaTeX? `python3 render.py` uses headless Chrome instead (two-column,
styled; less ATS-safe, fine for human recruiters).

## 6. Seed the company database

```bash
.venv/bin/python -m jobpilot.seed --quick      # verified list only, fast
.venv/bin/python -m jobpilot.seed              # + probe ~6,000 more slugs (minutes)
```

Have you applied to jobs before? Put your history at `applications.csv`
(header `date_applied,company,role,outcome,ats,url,application_id`) before
seeding. It powers the duplicate guard and registers every company you ever
applied to, including Workday tenants.

Track any specific company later with:

```bash
.venv/bin/python -m jobpilot add-company <slug> <ats>
# Workday needs host+site metadata:
.venv/bin/python -m jobpilot add-company acme workday \
  --meta '{"host":"acme.wd5.myworkdayjobs.com","site":"External"}'
```

## 7. Workday credentials (optional)

Every Workday tenant needs its own account. When a tenant offers Google SSO
and the Playwright browser profile carries a Google session, the agent signs
in that way. Otherwise it uses one email + one standard password from
`secrets/workday.json`:

```bash
cp secrets/workday.json.example secrets/workday.json   # then edit
```

Pick a password you use nowhere else: it sits in plain text on disk
(gitignored, never leaves the machine) and gets typed into many third-party
Workday tenants. Without this file, Workday jobs simply park in the
needs_human queue, so skipping this section is safe.

## 8. Notifications (optional, but do it before going hands-off)

```bash
mkdir -p ~/.config/jobpilot
cp notify/notify.sh ~/.config/jobpilot/notify.sh
cp notify/notify.env.example ~/.config/jobpilot/notify.env
```

Edit `~/.config/jobpilot/notify.env`: pick a long random `NTFY_TOPIC`
(`openssl rand -hex 12`), then subscribe to the same topic in the ntfy app on
your phone. The topic name is the only access control; treat it like a
password. Test:

```bash
.venv/bin/python -m jobpilot.notify "JobPilot" "setup test" 3 tada
```

Config lives in `$HOME`, not the repo, so alerts still fire when the repo's
disk is unreadable.

## 9. First runs, supervised

```bash
.venv/bin/python -m jobpilot.discover        # first sweep takes a while
.venv/bin/python -m jobpilot.match           # filter + LLM score
.venv/bin/python -m jobpilot status
.venv/bin/python -m jobpilot list queued
.venv/bin/python -m jobpilot.applier --dry-run
.venv/bin/python -m jobpilot.applier         # watch the first real batch
```

Review `out/applications/<id>-<company>.md` and the screenshot after each
job. Adjust `profile.md` and `scoring.calibration` until the answers and the
queue both look right. Do not schedule anything before this step.

## 10. Schedule it (launchd)

```bash
mkdir -p ~/Library/LaunchAgents
for t in launchd/*.plist.template; do
  name=$(basename "$t" .template)
  sed "s|{{ROOT}}|$PWD|g" "$t" > ~/Library/LaunchAgents/$name
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$name
done
echo "$PWD" > ~/.config/jobpilot/root      # lets /jobpilot:status find you
```

| job | plist | cadence |
|---|---|---|
| discover+match | com.jobpilot.cycle | every 3h |
| apply batch | com.jobpilot.apply | 09:41, 12:19, 15:07, 17:53, 21:31 |
| digest | com.jobpilot.digest | 08:05 |

Read the "Going hands-off" section of the README for the requirements that
keep this actually running: machine awake, Full Disk Access for /bin/zsh on
external volumes, Claude usage limits, browser logins, notifications.

## Troubleshooting

- **launchd runs nothing / logs "ROOT not accessible"**: the volume is
  unmounted or `/bin/zsh` lacks Full Disk Access. Grant it in System
  Settings > Privacy & Security > Full Disk Access.
- **`claude: command not found` in scheduled runs**: launchd has a minimal
  PATH. Set absolute `claude_bin` paths in `config.yaml`.
- **Everything queues but nothing applies**: check
  `logs/usage-limit-until` (plan window exhausted; it clears itself) and
  `~/Library/Logs/jobpilot-apply.log`.
- **Workday jobs all land in needs_human**: create `secrets/workday.json`
  from the example. Tenants that offer Google SSO sign in with the browser's
  Google session instead.
- **Scoring errors**: run `.venv/bin/python -m jobpilot.match` by hand and
  read the output; usually the CLI is logged out or the plan window is
  exhausted.
- **A board 404s forever**: it deactivates automatically after 8 consecutive
  failures. `jobpilot add-company` re-adds if it comes back.

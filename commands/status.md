---
description: Show the JobPilot pipeline status - queue, applied, needs-human, recent runs
---

Find the JobPilot install directory: read `~/.config/jobpilot/root`; if it
does not exist, ask the user where JobPilot is installed (and offer to run
`/jobpilot:setup` if it is not).

Then run and interpret for the user:

```bash
cd "$(cat ~/.config/jobpilot/root)"
.venv/bin/python -m jobpilot status
.venv/bin/python -m jobpilot list queued --limit 15
.venv/bin/python -m jobpilot list needs_human --limit 15
```

Summarize: how many applied today, what is queued next, what is blocked
waiting for the human (captchas, logins, odd ATSes) and why, and whether the
recent discover/match/apply runs succeeded. If a run failed, show the tail of
the matching log in `logs/` and `~/Library/Logs/jobpilot-*.log`.

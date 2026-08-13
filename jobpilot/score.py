"""LLM fit scoring through the Claude Code CLI on the subscription (never an API key)."""
import json
import os
import re
import subprocess

RUBRIC = """You are scoring job postings for one candidate. Output ONLY a JSON \
array, no prose, no code fences. Do not use any tools.

CANDIDATE
{summary}

CALIBRATION (rules from the candidate's config; follow them exactly)
{calibration}

SPONSORSHIP (hard rule, applies only if the candidate needs sponsorship per the \
summary above): if the text says the employer will not sponsor / requires \
citizenship or permanent residency / needs a security clearance, set score 0 \
and sponsorship_risk "stated_no". If it hints (e.g. "must be authorized without \
sponsorship now or in the future"), cap score at 25 and use "hinted_no". \
Otherwise "none_seen".

JOBS
{jobs}

For EVERY job return one object:
{{"k": "<same k>", "score": <0-100 integer>, "sponsorship_risk": \
"none_seen|hinted_no|stated_no", "reason": "<=140 chars, concrete>"}}
Return the JSON array only."""


def score_batch(jobs, cfg):
    """jobs: list of sqlite rows. Returns {job_id: {score, sponsorship_risk, reason}}."""
    sc = cfg["scoring"]
    payload = []
    for j in jobs:
        payload.append({
            "k": j["id"],
            "title": j["title"],
            "company": j["company"],
            "location": (j["location"] or "")[:120],
            "salary": f"{j['salary_min']}-{j['salary_max']}"
                      if j["salary_min"] else "unlisted",
            "posted": j["posted_at"] or "",
            "jd": (j["jd_text"] or "")[: sc["jd_chars"]],
        })
    prompt = RUBRIC.format(summary=cfg["profile"]["summary"].strip(),
                           calibration=(sc.get("calibration") or "none").strip(),
                           jobs=json.dumps(payload, ensure_ascii=False))
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    proc = subprocess.run(
        [sc["claude_bin"], "-p", "--model", sc["model"]],
        input=prompt, capture_output=True, text=True,
        timeout=sc["timeout_s"], env=env, cwd=cfg["_root"],
    )
    out = proc.stdout.strip()
    if proc.returncode != 0:
        raise RuntimeError(f"claude scoring failed rc={proc.returncode}: "
                           f"{proc.stderr[:400] or out[:400]}")
    return _parse(out)


def _parse(out):
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        raise ValueError(f"no JSON array in scorer output: {out[:300]}")
    results = {}
    for item in json.loads(m.group(0)):
        k = item.get("k")
        if not k:
            continue
        results[k] = {
            "score": max(0, min(100, int(item.get("score", 0)))),
            "sponsorship_risk": item.get("sponsorship_risk", "none_seen"),
            "reason": (item.get("reason") or "")[:200],
        }
    return results


def is_usage_limit(text: str) -> bool:
    t = (text or "").lower()
    return any(s in t for s in (
        "usage limit", "rate limit", "hit your limit", "limit reached",
        "out of extended usage", "resets at",
    ))

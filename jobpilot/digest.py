"""Daily digest: what happened, what's queued, what needs the human's hands."""
import json
import os
import subprocess
from datetime import datetime, timedelta

from . import db, notify


def build(conn, cfg):
    today = datetime.now().strftime("%Y-%m-%d")
    yday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    def rows(q, *a):
        return conn.execute(q, a).fetchall()

    applied = rows(
        "SELECT * FROM jobs WHERE status='applied' AND applied_at >= ?"
        " ORDER BY applied_at DESC", yday)
    needs = rows(
        "SELECT * FROM jobs WHERE status='needs_human' ORDER BY score DESC"
        " LIMIT 25")
    # referral-flagged companies are applied to normally; the digest reminds
    # you to also ask a contact for a referral.
    referrals = rows(
        "SELECT * FROM jobs WHERE filter_reason='referral_wanted'"
        " AND status IN ('applied','queued','needs_human')"
        " ORDER BY updated_at DESC LIMIT 25")
    queued = rows(
        "SELECT * FROM jobs WHERE status='queued' ORDER BY score DESC LIMIT 20")
    near = rows(
        "SELECT * FROM jobs WHERE status='scored' AND score >= ?"
        " ORDER BY score DESC LIMIT 15", cfg["scoring"]["review_threshold"])
    failed = rows(
        "SELECT * FROM jobs WHERE status='failed' AND updated_at >= ?"
        " ORDER BY updated_at DESC LIMIT 10", yday)
    counts = {r["status"]: r["c"] for r in rows(
        "SELECT status, COUNT(*) c FROM jobs GROUP BY status")}
    runs = rows("SELECT * FROM runs WHERE started_at >= ? ORDER BY id DESC", yday)

    def line(j, extra=""):
        sal = f" ${j['salary_min']//1000}k-{j['salary_max']//1000}k" \
            if j["salary_min"] and j["salary_max"] else ""
        return (f"- [{j['score'] or '--'}] **{j['company']}** — {j['title']}"
                f" ({j['ats']}{sal}) {extra}\n  {j['url']}\n")

    md = [f"# Job pipeline digest — {today}\n"]
    md.append(f"**Applied since yesterday: {len(applied)}** · queue: "
              f"{counts.get('queued', 0)} · needs you: "
              f"{counts.get('needs_human', 0)} · total tracked: "
              f"{sum(counts.values())}\n")
    if applied:
        md.append("## Applied\n" + "".join(
            line(j, f"→ {j['apply_notes'] or 'ok'}") for j in applied))
    if referrals:
        md.append("## ASK FOR A REFERRAL — applied already, ping your friend\n"
                  + "".join(line(j, f"[{j['status']}]") for j in referrals))
    if needs:
        md.append("## Needs your hands (captcha / login / odd ATS)\n" + "".join(
            line(j, f"→ {j['apply_notes'] or ''}") for j in needs))
    if queued:
        md.append("## Up next in the auto-apply queue\n" + "".join(
            line(j) for j in queued))
    if near:
        md.append("## Near-misses (promote with: jobpilot promote <id>)\n"
                  + "".join(line(j, f"`{j['id']}` {j['score_reasons'] or ''}")
                            for j in near))
    if failed:
        md.append("## Failed attempts\n" + "".join(
            line(j, f"→ {j['apply_notes'] or ''}") for j in failed))
    md.append("## Pipeline runs (24h)\n" + "".join(
        f"- {r['kind']} {r['started_at']} ok={r['ok']}: "
        f"{(r['summary'] or '')[:160]}\n" for r in runs))
    md.append(f"\nStatus counts: `{json.dumps(counts)}`\n")
    return "\n".join(md), len(applied), counts, referrals


def main():
    from .config import load
    cfg = load()
    conn = db.connect(cfg["_db_path"])
    md, applied_n, counts, referrals = build(conn, cfg)
    out_dir = os.path.join(cfg["_root"], "out")
    os.makedirs(out_dir, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(out_dir, f"digest-{today}.md")
    with open(path, "w") as f:
        f.write(md)
    # prune old digests
    keep = cfg["digest"].get("keep_days", 30)
    cutoff = (datetime.now() - timedelta(days=keep)).strftime("%Y-%m-%d")
    for name in os.listdir(out_dir):
        if name.startswith("digest-") and name < f"digest-{cutoff}":
            os.remove(os.path.join(out_dir, name))
    if cfg["digest"].get("notify", True):
        msg = (f"{applied_n} applied · {counts.get('queued', 0)} queued · "
               f"{counts.get('needs_human', 0)} need you")
        subprocess.run(["osascript", "-e",
                        f'display notification "{msg}" with title '
                        f'"JobPilot digest" sound name "Glass"'],
                       capture_output=True)
        stuck = conn.execute(
            "SELECT company, title, apply_notes FROM jobs"
            " WHERE status='needs_human' ORDER BY score DESC LIMIT 5"
        ).fetchall()
        body = [msg, ""]
        if referrals:
            body.append("Ask for a referral:")
            body += [f"★ {r['company']} · {r['title']}" for r in referrals[:5]]
            body.append("")
        if stuck:
            body.append("Waiting on you:")
            body += [f"⚠ {r['company']} · {r['title']}"
                     f"{' — ' + r['apply_notes'][:60] if r['apply_notes'] else ''}"
                     for r in stuck]
        else:
            body.append("Nothing waiting on you.")
        notify.push(f"📋 JobPilot digest — {applied_n} applied yesterday",
                    "\n".join(body), notify.P_LOW, ["clipboard"])
    print(path)


if __name__ == "__main__":
    main()

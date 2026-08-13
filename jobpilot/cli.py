"""jobpilot CLI: python -m jobpilot <command>"""
import argparse
import json
import sys

from . import db
from .config import load


def cmd_status(conn, cfg, args):
    rows = conn.execute(
        "SELECT status, COUNT(*) c FROM jobs GROUP BY status ORDER BY c DESC"
    ).fetchall()
    total = sum(r["c"] for r in rows)
    print(f"jobs tracked: {total}")
    for r in rows:
        print(f"  {r['status']:24s} {r['c']}")
    comp = conn.execute(
        "SELECT ats, COUNT(*) c, SUM(active) a FROM companies GROUP BY ats"
    ).fetchall()
    print("companies (active/total):")
    for r in comp:
        print(f"  {r['ats']:16s} {r['a'] or 0}/{r['c']}")
    last = conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT 5").fetchall()
    print("recent runs:")
    for r in last:
        flag = "ok" if r["ok"] else "FAILED"
        print(f"  {r['started_at']} {r['kind']:9s} [{flag}]"
              f" {(r['summary'] or '')[:120]}")


def cmd_list(conn, cfg, args):
    rows = conn.execute(
        "SELECT id, score, company, title, ats, status, apply_notes,"
        " filter_reason FROM jobs WHERE status=? ORDER BY score DESC,"
        " updated_at DESC LIMIT ?", (args.status, args.limit)).fetchall()
    for r in rows:
        note = r["apply_notes"] or r["filter_reason"] or ""
        print(f"{r['id']}  [{r['score'] if r['score'] is not None else '--':>3}]"
              f" {r['company'][:28]:28s} {r['title'][:44]:44s} {r['ats']:12s}"
              f" {note[:50]}")


def cmd_show(conn, cfg, args):
    r = conn.execute("SELECT * FROM jobs WHERE id=?", (args.id,)).fetchone()
    if not r:
        sys.exit(f"no job {args.id}")
    d = dict(r)
    d["jd_text"] = (d.get("jd_text") or "")[:1500]
    print(json.dumps(d, indent=2, default=str))


def cmd_promote(conn, cfg, args):
    db.set_status(conn, args.id, "queued")
    conn.commit()
    print(f"{args.id} -> queued")


def cmd_skip(conn, cfg, args):
    db.set_status(conn, args.id, "filtered_out",
                  filter_reason=f"manual:{args.reason}")
    conn.commit()
    print(f"{args.id} -> skipped ({args.reason})")


def cmd_add_company(conn, cfg, args):
    meta = args.meta or ""
    if db.add_company(conn, args.slug, args.ats, args.name or args.slug,
                      source="manual", meta=meta):
        conn.commit()
        print(f"added {args.slug} ({args.ats})")
    else:
        print("already present")


def main():
    p = argparse.ArgumentParser(prog="jobpilot")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    lp = sub.add_parser("list")
    lp.add_argument("status")
    lp.add_argument("--limit", type=int, default=40)
    sp = sub.add_parser("show")
    sp.add_argument("id")
    pp = sub.add_parser("promote")
    pp.add_argument("id")
    kp = sub.add_parser("skip")
    kp.add_argument("id")
    kp.add_argument("reason", nargs="?", default="manual")
    ap = sub.add_parser("add-company")
    ap.add_argument("slug")
    ap.add_argument("ats")
    ap.add_argument("--name")
    ap.add_argument("--meta")
    args = p.parse_args()

    cfg = load()
    conn = db.connect(cfg["_db_path"])
    {
        "status": cmd_status, "list": cmd_list, "show": cmd_show,
        "promote": cmd_promote, "skip": cmd_skip,
        "add-company": cmd_add_company,
    }[args.cmd](conn, cfg, args)


if __name__ == "__main__":
    main()

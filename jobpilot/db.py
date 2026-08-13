"""SQLite store for the job pipeline. One file, no ORM."""
import os
import sqlite3
import hashlib
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  canonical_url TEXT UNIQUE,
  url TEXT,
  apply_url TEXT,
  source TEXT,
  company TEXT,
  company_slug TEXT,
  title TEXT,
  ats TEXT,
  location TEXT,
  workplace_type TEXT,
  salary_min INTEGER,
  salary_max INTEGER,
  posted_at TEXT,
  discovered_at TEXT,
  jd_text TEXT,
  status TEXT DEFAULT 'discovered',
  score INTEGER,
  score_reasons TEXT,
  sponsorship TEXT,
  filter_reason TEXT,
  applied_at TEXT,
  apply_notes TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);

CREATE TABLE IF NOT EXISTS companies (
  slug TEXT,
  ats TEXT,
  name TEXT,
  active INTEGER DEFAULT 1,
  meta TEXT,
  last_polled TEXT,
  last_job_count INTEGER,
  consecutive_failures INTEGER DEFAULT 0,
  added_at TEXT,
  source TEXT,
  PRIMARY KEY (slug, ats)
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,
  started_at TEXT,
  finished_at TEXT,
  ok INTEGER,
  summary TEXT
);
"""

# Job status lifecycle:
#   discovered      -> just ingested, not yet filtered
#   filtered_out    -> failed a hard rule (filter_reason says which)
#   skipped_no_sponsorship -> JD or form says employer won't sponsor
#   duplicate       -> already applied per applications.csv / db
#   scored          -> passed filters, has an LLM score below apply threshold
#   queued          -> score >= threshold, waiting for the applier
#   applying        -> an apply run is in flight
#   applied         -> submitted successfully
#   needs_human     -> captcha / login wall / email verification / weird form
#   failed          -> apply attempt errored (apply_notes has details)
#   expired         -> posting disappeared from the board


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_id(canonical_url: str) -> str:
    return hashlib.sha1(canonical_url.encode()).hexdigest()[:16]


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def upsert_job(conn, row: dict) -> bool:
    """Insert a job if its canonical URL is new. Returns True if inserted."""
    row = dict(row)
    row.setdefault("discovered_at", utcnow())
    row["updated_at"] = utcnow()
    row["id"] = job_id(row["canonical_url"])
    cols = ",".join(row.keys())
    ph = ",".join("?" * len(row))
    try:
        conn.execute(
            f"INSERT INTO jobs ({cols}) VALUES ({ph})", list(row.values())
        )
        return True
    except sqlite3.IntegrityError:
        return False


def set_status(conn, jid: str, status: str, **fields):
    fields["status"] = status
    fields["updated_at"] = utcnow()
    sets = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*fields.values(), jid])


def add_company(conn, slug: str, ats: str, name: str = "", source: str = "",
                meta: str = "") -> bool:
    try:
        conn.execute(
            "INSERT INTO companies (slug, ats, name, meta, added_at, source)"
            " VALUES (?,?,?,?,?,?)",
            (slug, ats, name or slug, meta, utcnow(), source),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def log_run(conn, kind: str, started_at: str, ok: bool, summary: str):
    conn.execute(
        "INSERT INTO runs (kind, started_at, finished_at, ok, summary)"
        " VALUES (?,?,?,?,?)",
        (kind, started_at, utcnow(), 1 if ok else 0, summary),
    )
    conn.commit()

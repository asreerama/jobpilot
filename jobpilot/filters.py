"""Hard, deterministic gates. Anything ambiguous falls through to the LLM scorer."""
import csv
import re
from datetime import datetime, timezone

from .normalize import canonical_url, looks_us


def _compile(patterns):
    return [re.compile(p, re.I) for p in patterns or []]


REMOTE_RE = re.compile(r"remote|anywhere|distributed|work from home|wfh", re.I)
YOE_RE = re.compile(
    r"(?:minimum of|at least|requires?|require)\s*(\d{1,2})\+?\s*years?"
    r"|(\d{1,2})\+?\s*(?:or more\s*)?years?[^.\n]{0,50}?"
    r"(?:of\s+)?(?:product|pm|professional|relevant|industry)?\s*experience",
    re.I)


class Filters:
    def __init__(self, cfg):
        c = cfg["criteria"]
        self.include = _compile(c.get("title_include_patterns"))
        self.exclude = _compile(c.get("title_exclude_patterns"))
        self.blockers = _compile(c.get("sponsorship_blockers"))
        self.min_ceiling = c.get("min_salary_ceiling") or 0
        self.max_age_days = c.get("max_posting_age_days") or 365
        self.us_only = c.get("us_only", True)
        self.max_yoe = c.get("max_required_yoe") or 99
        self.require_jd = c.get("require_jd", True)
        self.min_jd_chars = c.get("min_jd_chars") or 200
        self.bay_or_remote = c.get("bay_area_or_remote", False)
        self.bay_hints = _compile(c.get("bay_area_hints"))
        self.referral = _compile(c.get("referral_flag_companies"))
        self.blocklist = _compile(c.get("company_blocklist"))
        self.applied_urls, self.applied_pairs = load_history(cfg["_csv_path"])

    def required_yoe(self, jd: str):
        """Max years-of-experience the JD demands, best effort."""
        worst = None
        for m in YOE_RE.finditer(jd or ""):
            n = int(m.group(1) or m.group(2))
            if 1 <= n <= 20 and (worst is None or n > worst):
                worst = n
        return worst

    def location_ok(self, job) -> bool:
        loc = (job["location"] or "") + " " + (job["workplace_type"] or "")
        if not loc.strip():
            return True  # unknown -> scorer judges from JD
        if REMOTE_RE.search(loc):
            return True
        return any(p.search(loc) for p in self.bay_hints)

    def title_ok(self, title: str) -> bool:
        t = title or ""
        return any(p.search(t) for p in self.include) and not any(
            p.search(t) for p in self.exclude
        )

    def title_gate(self):
        """Callable for pollers to pre-filter at ingest."""
        return self.title_ok

    def check(self, job) -> tuple:
        """Returns (verdict, reason). verdict in: pass, filtered_out,
        skipped_no_sponsorship, duplicate."""
        title = job["title"] or ""
        company = job["company"] or ""
        if any(p.search(company) for p in self.blocklist):
            return ("filtered_out", "company_blocklist")
        # 2026-08-11: referral companies are applied to like any other. The tag
        # rides along so the digest can tell him to also ask for a referral.
        referral = any(p.search(company) for p in self.referral)
        if not self.title_ok(title):
            return ("filtered_out", "title")
        if self.us_only and not looks_us(job["location"] or ""):
            return ("filtered_out", f"non_us_location:{(job['location'] or '')[:60]}")
        if self.bay_or_remote and not self.location_ok(job):
            return ("filtered_out", f"not_bay_or_remote:{(job['location'] or '')[:50]}")
        yoe = self.required_yoe(job["jd_text"] or "")
        if yoe and yoe > self.max_yoe:
            return ("filtered_out", f"requires_{yoe}yoe")
        smax = job["salary_max"]
        if smax and smax < self.min_ceiling:
            return ("filtered_out", f"salary_ceiling:{smax}")
        posted = job["posted_at"]
        if posted:
            try:
                dt = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
                if dt.tzinfo is None:  # date-only strings parse naive
                    dt = dt.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - dt).days
                if age > self.max_age_days:
                    return ("filtered_out", f"stale:{age}d")
            except ValueError:
                pass
        cu = job["canonical_url"]
        if cu in self.applied_urls:
            return ("duplicate", "url_in_history")
        pair = ((job["company"] or "").strip().lower(), title.strip().lower())
        if pair in self.applied_pairs:
            return ("duplicate", "company+title_in_history")
        jd = (job["jd_text"] or "").strip()
        # Fail closed when there is no job description. Without one, BOTH the
        # years-of-experience cap above and the sponsorship-blocker scan below
        # silently pass everything: required_yoe("") is None and no blocker can
        # match empty text. That is how over-cap roles and no-sponsorship
        # postings reach the applier. LinkedIn-sourced jobs rarely carry a JD;
        # bin/backfill_jd.py fetches descriptions for rows parked here.
        if self.require_jd and len(jd) < self.min_jd_chars:
            return ("needs_jd", f"no_jd:{len(jd)}chars")
        for p in self.blockers:
            m = p.search(jd)
            if m:
                return ("skipped_no_sponsorship", f"jd_blocker:{m.group(0)[:60]}")
        return ("pass", "referral_wanted" if referral else "")


def load_history(csv_path):
    """applications.csv is the source of truth for what was already applied to."""
    urls, pairs = set(), set()
    try:
        with open(csv_path, newline="") as f:
            for row in csv.DictReader(f):
                u = (row.get("url") or "").strip()
                if u:
                    urls.add(canonical_url(u))
                comp = (row.get("company") or "").strip().lower()
                role = (row.get("role") or "").strip().lower()
                if comp and role:
                    pairs.add((comp, role))
    except FileNotFoundError:
        pass
    return urls, pairs

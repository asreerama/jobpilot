"""Discovery: poll ATS boards for tracked companies + sweep aggregators via JobSpy.

JobSpy results feed the flywheel: any direct ATS link it surfaces registers that
company in the companies table, so future postings arrive via the cheap poller.
"""
import json
import time
import traceback

from . import db
from .boards import POLLERS, poll_workday, extract_salary
from .filters import Filters
from .normalize import canonical_url, detect_ats

MAX_FAILURES = 8  # deactivate a company slug after this many consecutive errors


def run_boards(conn, cfg, filters):
    import os
    bcfg = cfg["discovery"]["boards"]
    delay = bcfg.get("per_company_delay_s", 0.7)
    timeout = bcfg.get("request_timeout_s", 20)
    budget = int(os.environ.get("JOBPILOT_POLL_BUDGET",
                                bcfg.get("poll_budget_per_cycle", 1200)))
    gate = filters.title_gate()
    inserted = polled = errors = 0
    # round-robin: least recently polled first, budget per cycle
    rows = conn.execute(
        "SELECT * FROM companies WHERE active=1 ORDER BY last_polled IS NOT NULL,"
        " last_polled LIMIT ?", (budget,)).fetchall()
    for c in rows:
        ats, slug = c["ats"], c["slug"]
        try:
            if ats == "workday":
                if not cfg["discovery"]["workday"].get("enabled", True):
                    continue
                meta = json.loads(c["meta"] or "{}")
                if "host" not in meta:
                    continue
                jobs = poll_workday(slug, meta,
                                    cfg["discovery"]["workday"]["search_terms"],
                                    timeout)
            elif ats in POLLERS:
                if ats == "smartrecruiters":
                    jobs = POLLERS[ats](slug, timeout, title_match=gate)
                else:
                    jobs = POLLERS[ats](slug, timeout)
            else:
                continue
            count = 0
            for j in jobs:
                count += 1
                if not gate(j["title"]):
                    continue
                j["canonical_url"] = canonical_url(j["url"])
                if db.upsert_job(conn, j):
                    inserted += 1
            conn.execute(
                "UPDATE companies SET last_polled=?, last_job_count=?,"
                " consecutive_failures=0 WHERE slug=? AND ats=?",
                (db.utcnow(), count, slug, ats))
            polled += 1
        except Exception as e:
            errors += 1
            # 404 = board gone / junk slug: kill fast. Transient errors: slowly.
            is_404 = "404" in str(e)
            fails = (c["consecutive_failures"] or 0) + (4 if is_404 else 1)
            active = 0 if fails >= MAX_FAILURES else 1
            conn.execute(
                "UPDATE companies SET consecutive_failures=?, active=?,"
                " last_polled=? WHERE slug=? AND ats=?",
                (fails, active, db.utcnow(), slug, ats))
        conn.commit()
        time.sleep(delay)
    return {"polled": polled, "inserted": inserted, "errors": errors,
            "companies": len(rows)}


def run_simplify(conn, cfg, filters):
    """SimplifyJobs listings.json: one GET, includes a sponsorship field."""
    import requests
    scfg = cfg["discovery"].get("simplify", {})
    if not scfg.get("enabled", True):
        return {"skipped": True}
    gate = filters.title_gate()
    inserted = 0
    url = ("https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/"
           "dev/.github/scripts/listings.json")
    listings = requests.get(url, timeout=60).json()
    for it in listings:
        if not it.get("active") or not it.get("is_visible"):
            continue
        title = (it.get("title") or "").strip()
        if not gate(title):
            continue
        sponsorship = it.get("sponsorship") or ""
        if sponsorship in ("Does Not Offer Sponsorship",
                           "U.S. Citizenship is Required"):
            continue
        u = it.get("url") or ""
        if not u:
            continue
        ats, slug = detect_ats(u)
        posted = it.get("date_posted")
        if isinstance(posted, (int, float)):
            posted = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(posted))
        job = {
            "company": it.get("company_name") or "",
            "company_slug": slug or "",
            "title": title,
            "url": u,
            "apply_url": u,
            "ats": ats,
            "location": "; ".join(it.get("locations") or []),
            "workplace_type": "",
            "salary_min": None, "salary_max": None,
            "posted_at": posted,
            "jd_text": "",
            "sponsorship": "yes" if sponsorship == "Offers Sponsorship" else None,
            "source": "simplify",
            "canonical_url": canonical_url(u),
        }
        if db.upsert_job(conn, job):
            inserted += 1
    conn.commit()
    return {"inserted": inserted}


def run_jobspy(conn, cfg, filters):
    import os
    jcfg = cfg["discovery"]["jobspy"]
    if not jcfg.get("enabled", True) or os.environ.get("JOBPILOT_SKIP_JOBSPY"):
        return {"skipped": True}
    from jobspy import scrape_jobs  # heavy import; keep it lazy

    gate = filters.title_gate()
    inserted = new_companies = 0
    seen_errors = []
    for search in jcfg["searches"]:
        sites = list(jcfg.get("sites", ["indeed"]))
        try:
            df = scrape_jobs(
                site_name=sites,
                search_term=search.get("search_term"),
                google_search_term=search.get("google_search_term"),
                location=search.get("location"),
                is_remote=search.get("is_remote", False),
                results_wanted=jcfg.get("results_wanted", 60),
                hours_old=jcfg.get("hours_old", 72),
                country_indeed="USA",
                linkedin_fetch_description=False,
                verbose=0,
            )
        except Exception as e:
            seen_errors.append(f"{search.get('search_term')}: {e}")
            continue
        for _, r in df.iterrows():
            title = str(r.get("title") or "")
            if not gate(title):
                continue
            direct = r.get("job_url_direct")
            direct = str(direct) if direct and str(direct) != "nan" else ""
            url = str(r.get("job_url") or "")
            best = direct or url
            ats, slug = detect_ats(best)
            # register newly seen ATS companies for future board polling
            if slug and ats in ("greenhouse", "lever", "ashby", "smartrecruiters"):
                if db.add_company(conn, slug, ats,
                                  str(r.get("company") or slug),
                                  source="jobspy"):
                    new_companies += 1
            lo = r.get("min_amount")
            hi = r.get("max_amount")
            lo = int(lo) if lo and str(lo) != "nan" else None
            hi = int(hi) if hi and str(hi) != "nan" else None
            jd = str(r.get("description") or "")
            if jd == "nan":
                jd = ""
            if not lo:
                lo, hi = extract_salary(jd)
            posted = r.get("date_posted")
            job = {
                "company": str(r.get("company") or ""),
                "company_slug": slug or "",
                "title": title.strip(),
                "url": best,
                "apply_url": direct or url,
                "ats": ats,
                "location": str(r.get("location") or ""),
                "workplace_type": "remote" if r.get("is_remote") else "",
                "salary_min": lo, "salary_max": hi,
                "posted_at": str(posted) if posted is not None else None,
                "jd_text": jd[:20000],
                "source": f"jobspy:{r.get('site')}",
                "canonical_url": canonical_url(best),
            }
            if db.upsert_job(conn, job):
                inserted += 1
        conn.commit()
        time.sleep(2)
    return {"inserted": inserted, "new_companies": new_companies,
            "errors": seen_errors}


def main():
    import sys
    from .config import load
    cfg = load()
    jobspy_only = "--jobspy-only" in sys.argv
    if "--hours" in sys.argv:
        cfg["discovery"]["jobspy"]["hours_old"] = int(
            sys.argv[sys.argv.index("--hours") + 1])
    conn = db.connect(cfg["_db_path"])
    filters = Filters(cfg)
    started = db.utcnow()
    summary = {}
    ok = True
    try:
        if not jobspy_only and cfg["discovery"]["boards"].get("enabled", True):
            summary["boards"] = run_boards(conn, cfg, filters)
        if not jobspy_only:
            try:
                summary["simplify"] = run_simplify(conn, cfg, filters)
            except Exception as e:
                summary["simplify"] = {"error": str(e)[:200]}
        summary["jobspy"] = run_jobspy(conn, cfg, filters)
    except Exception:
        ok = False
        summary["fatal"] = traceback.format_exc(limit=3)
    db.log_run(conn, "discover", started, ok, json.dumps(summary, default=str))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()

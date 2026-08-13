"""Pollers for public ATS job-board JSON APIs. No auth, no browser, cheap.

Each poller yields normalized job dicts:
  {company, company_slug, title, url, apply_url, ats, location,
   workplace_type, salary_min, salary_max, posted_at, jd_text, source}
"""
import json
import re
import time
import requests

from .normalize import html_to_text

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

SALARY_RE = re.compile(
    r"\$\s?(\d{2,3})[,.]?(\d{3})(?:\s*(?:-|–|—|to)\s*\$?\s?(\d{2,3})[,.]?(\d{3}))?"
)


def extract_salary(text: str):
    """Best-effort (min, max) annual USD from free text."""
    if not text:
        return (None, None)
    best = (None, None)
    for m in SALARY_RE.finditer(text[:12000]):
        lo = int(m.group(1) + m.group(2))
        hi = int(m.group(3) + m.group(4)) if m.group(3) else lo
        if 40000 <= lo <= 900000:
            if best[1] is None or hi > best[1]:
                best = (lo, hi)
    return best


def _get(url, timeout, **kw):
    r = requests.get(url, headers=UA, timeout=timeout, **kw)
    r.raise_for_status()
    return r


def poll_greenhouse(slug, timeout=20):
    r = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
             timeout, params={"content": "true"})
    for j in r.json().get("jobs", []):
        jd = html_to_text(j.get("content", ""))
        lo, hi = extract_salary(jd)
        yield {
            "company": j.get("company_name") or slug,
            "company_slug": slug,
            "title": (j.get("title") or "").strip(),
            "url": j.get("absolute_url"),
            "apply_url": j.get("absolute_url"),
            "ats": "greenhouse",
            "location": (j.get("location") or {}).get("name", ""),
            "workplace_type": "",
            "salary_min": lo, "salary_max": hi,
            "posted_at": j.get("first_published") or j.get("updated_at"),
            "jd_text": jd,
            "source": "boards:greenhouse",
        }


def poll_lever(slug, timeout=20):
    r = _get(f"https://api.lever.co/v0/postings/{slug}", timeout,
             params={"mode": "json"})
    for j in r.json():
        cats = j.get("categories") or {}
        jd = j.get("descriptionPlain") or html_to_text(j.get("description", ""))
        for lst in j.get("lists") or []:
            jd += "\n" + html_to_text(lst.get("content", ""), 4000)
        sal = j.get("salaryRange") or {}
        lo, hi = sal.get("min"), sal.get("max")
        if not lo:
            lo, hi = extract_salary(jd)
        posted = j.get("createdAt")
        if isinstance(posted, (int, float)):
            posted = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(posted / 1000))
        yield {
            "company": slug,
            "company_slug": slug,
            "title": (j.get("text") or "").strip(),
            "url": j.get("hostedUrl"),
            "apply_url": j.get("applyUrl") or j.get("hostedUrl"),
            "ats": "lever",
            "location": cats.get("location", "") or j.get("country", ""),
            "workplace_type": (j.get("workplaceType") or "").lower(),
            "salary_min": lo, "salary_max": hi,
            "posted_at": posted,
            "jd_text": jd[:20000],
            "source": "boards:lever",
        }


def poll_ashby(slug, timeout=20):
    r = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
             timeout, params={"includeCompensation": "true"})
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = (j.get("compensation") or {})
        summary = comp.get("scrapeableCompensationSalarySummary") or ""
        lo, hi = extract_salary(summary)
        jd = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))
        if not lo:
            lo, hi = extract_salary(jd)
        locs = [j.get("location") or ""]
        locs += [s.get("location", "") for s in j.get("secondaryLocations") or []]
        yield {
            "company": slug,
            "company_slug": slug,
            "title": (j.get("title") or "").strip(),
            "url": j.get("jobUrl"),
            "apply_url": j.get("applyUrl") or j.get("jobUrl"),
            "ats": "ashby",
            "location": "; ".join(x for x in locs if x),
            "workplace_type": "remote" if j.get("isRemote") else
                              (j.get("workplaceType") or "").lower(),
            "salary_min": lo, "salary_max": hi,
            "posted_at": j.get("publishedAt"),
            "jd_text": jd[:20000],
            "source": "boards:ashby",
        }


def poll_smartrecruiters(slug, timeout=20, title_match=None):
    offset = 0
    while True:
        r = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                 timeout, params={"limit": 100, "offset": offset})
        data = r.json()
        for j in data.get("content", []):
            loc = j.get("location") or {}
            if (loc.get("country") or "").lower() not in ("us", "usa", ""):
                continue
            title = (j.get("name") or "").strip()
            jd = ""
            # JD costs an extra request; only fetch for plausible titles
            if title_match is None or title_match(title):
                try:
                    d = _get(
                        f"https://api.smartrecruiters.com/v1/companies/{slug}"
                        f"/postings/{j['id']}", timeout).json()
                    secs = ((d.get("jobAd") or {}).get("sections") or {})
                    jd = html_to_text(" ".join(
                        (secs.get(k) or {}).get("text", "")
                        for k in ("companyDescription", "jobDescription",
                                  "qualifications", "additionalInformation")))
                except Exception:
                    pass
            lo, hi = extract_salary(jd)
            city = ", ".join(x for x in (loc.get("city"), loc.get("region")) if x)
            yield {
                "company": j.get("company", {}).get("name") or slug,
                "company_slug": slug,
                "title": title,
                "url": f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                "apply_url": f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                "ats": "smartrecruiters",
                "location": city + (" (remote)" if loc.get("remote") else ""),
                "workplace_type": "remote" if loc.get("remote") else "",
                "salary_min": lo, "salary_max": hi,
                "posted_at": j.get("releasedDate"),
                "jd_text": jd[:20000],
                "source": "boards:smartrecruiters",
            }
        total = data.get("totalFound", 0)
        offset += 100
        if offset >= total:
            break


def poll_workday(slug, meta, search_terms, timeout=20):
    """meta: {"host": "procore.wd12.myworkdayjobs.com", "site": "procore_external_careers"}"""
    host, site = meta["host"], meta["site"]
    base = f"https://{host}/wday/cxs/{slug}/{site}"
    for term in search_terms:
        offset = 0
        while offset < 100:
            r = requests.post(
                f"{base}/jobs", headers={**UA, "Content-Type": "application/json"},
                json={"appliedFacets": {}, "limit": 20, "offset": offset,
                      "searchText": term},
                timeout=timeout)
            r.raise_for_status()
            data = r.json()
            posts = data.get("jobPostings", [])
            for j in posts:
                path = j.get("externalPath", "")
                if not path:
                    continue
                url = f"https://{host}/en-US/{site}{path}"
                jd = ""
                try:
                    d = _get(f"{base}{path}", timeout).json()
                    info = d.get("jobPostingInfo") or {}
                    jd = html_to_text(info.get("jobDescription", ""))
                except Exception:
                    pass
                lo, hi = extract_salary(jd)
                yield {
                    "company": slug,
                    "company_slug": slug,
                    "title": (j.get("title") or "").strip(),
                    "url": url,
                    "apply_url": url,
                    "ats": "workday",
                    "location": j.get("locationsText", ""),
                    "workplace_type": "",
                    "salary_min": lo, "salary_max": hi,
                    "posted_at": None,  # workday only gives "Posted N Days Ago"
                    "jd_text": jd[:20000],
                    "source": "boards:workday",
                }
            if len(posts) < 20:
                break
            offset += 20


def poll_workable(slug, timeout=20):
    r = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", timeout)
    data = r.json()
    name = data.get("name") or slug
    for j in data.get("jobs", []):
        loc = ", ".join(x for x in (j.get("city"), j.get("state"),
                                    j.get("country")) if x)
        country = (j.get("country") or "")
        if country and country.lower() not in ("united states", "us", "usa"):
            if not j.get("telecommuting"):
                continue
        jd = html_to_text(j.get("description", ""))
        lo, hi = extract_salary(jd)
        yield {
            "company": name,
            "company_slug": slug,
            "title": (j.get("title") or "").strip(),
            "url": j.get("url") or j.get("shortlink"),
            "apply_url": (j.get("application_url") or j.get("url")
                          or j.get("shortlink")),
            "ats": "workable",
            "location": loc + (" (remote)" if j.get("telecommuting") else ""),
            "workplace_type": "remote" if j.get("telecommuting") else "",
            "salary_min": lo, "salary_max": hi,
            "posted_at": j.get("published_on") or j.get("created_at"),
            "jd_text": jd[:20000],
            "source": "boards:workable",
        }


def poll_recruitee(slug, timeout=20):
    r = _get(f"https://{slug}.recruitee.com/api/offers/", timeout)
    for j in r.json().get("offers", []):
        country = (j.get("country") or "")
        if country and country.lower() not in ("united states", "us", "usa"):
            if not j.get("remote"):
                continue
        jd = html_to_text((j.get("description") or "")
                          + " " + (j.get("requirements") or ""))
        lo, hi = extract_salary(jd)
        yield {
            "company": j.get("company_name") or slug,
            "company_slug": slug,
            "title": (j.get("title") or "").strip(),
            "url": j.get("careers_url") or j.get("url"),
            "apply_url": j.get("careers_apply_url") or j.get("careers_url"),
            "ats": "recruitee",
            "location": j.get("location") or "",
            "workplace_type": "remote" if j.get("remote") else "",
            "salary_min": lo, "salary_max": hi,
            "posted_at": j.get("published_at") or j.get("created_at"),
            "jd_text": jd[:20000],
            "source": "boards:recruitee",
        }


POLLERS = {
    "greenhouse": poll_greenhouse,
    "lever": poll_lever,
    "ashby": poll_ashby,
    "smartrecruiters": poll_smartrecruiters,
    "workable": poll_workable,
    "recruitee": poll_recruitee,
}

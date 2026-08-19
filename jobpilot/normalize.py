"""URL canonicalization, ATS detection, and company-slug harvesting."""
import re
import html as html_mod
from urllib.parse import urlparse, urlunparse

ATS_PATTERNS = [
    ("greenhouse", r"(boards|job-boards)\.greenhouse\.io/(?:embed/job_app\?[^ ]*for=)?([a-z0-9-_]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([a-z0-9-_]+)"),
    ("lever", r"jobs\.lever\.co/([a-zA-Z0-9-_]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([a-zA-Z0-9-_.%]+)"),
    ("smartrecruiters", r"jobs\.smartrecruiters\.com/([a-zA-Z0-9-_]+)"),
    ("workday", r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com"),
    ("icims", r"([a-z0-9-]+)\.icims\.com"),
    ("workable", r"apply\.workable\.com/([a-zA-Z0-9-_]+)"),
    ("recruitee", r"([a-z0-9-]+)\.recruitee\.com"),
    ("bamboohr", r"([a-z0-9-]+)\.bamboohr\.com"),
    ("jobvite", r"jobs\.jobvite\.com/([a-zA-Z0-9-_]+)"),
    ("linkedin", r"linkedin\.com/jobs"),
]


def detect_ats(url: str):
    """Returns (ats, slug_or_None)."""
    if not url:
        return ("other", None)
    for ats, pat in ATS_PATTERNS:
        m = re.search(pat, url, re.I)
        if m:
            slug = m.group(1).lower() if m.groups() else None
            return (ats, slug)
    return ("other", None)


def canonical_url(url: str) -> str:
    """Stable identity for a posting: scheme+host+path, no query/fragment.

    Workday URLs keep the path only up to the job slug; LinkedIn keeps the
    numeric job id.
    """
    if not url:
        return url
    p = urlparse(url.strip())
    host = p.netloc.lower().replace("www.", "")
    path = re.sub(r"/+$", "", p.path)
    if "linkedin.com" in host:
        jid = re.search(r"(?:currentJobId=|/jobs/view/)(\d+)", url)
        if jid:
            return f"https://www.linkedin.com/jobs/view/{jid.group(1)}/"
    if "indeed.com" in host:  # identity lives in the jk query param
        jk = re.search(r"[?&]jk=([a-f0-9]+)", url)
        if jk:
            return f"https://www.indeed.com/viewjob?jk={jk.group(1)}"
    return urlunparse(("https", host, path, "", "", ""))


TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")


def html_to_text(s: str, limit: int = 20000) -> str:
    if not s:
        return ""
    s = html_mod.unescape(s)
    s = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = TAG_RE.sub(" ", s)
    s = WS_RE.sub(" ", s)
    s = re.sub(r"\n\s+", "\n", s).strip()
    return s[:limit]


US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC".split()
)
NON_US_HINTS = re.compile(
    r"\b(canada|united kingdom|\buk\b|london|toronto|vancouver|ontario|india|"
    r"bangalore|bengaluru|germany|berlin|france|paris|netherlands|amsterdam|"
    r"ireland|dublin|australia|sydney|singapore|japan|tokyo|brazil|mexico(?! city, mo)|"
    r"poland|spain|israel|tel aviv|emea|apac|latam)\b",
    re.I,
)
# City names that carry no state or country token in the posting. Without
# these, looks_us() rejected a plain "Sunnyvale" or "Broomfield HQ" as non-US
# and threw away good roles.
US_CITIES = (
    "san francisco|south san francisco|oakland|berkeley|emeryville|san jose|"
    "palo alto|mountain view|sunnyvale|santa clara|cupertino|menlo park|"
    "redwood city|san mateo|foster city|burlingame|fremont|milpitas|san carlos|"
    "los gatos|campbell|daly city|sausalito|mill valley|bay area|silicon valley|"
    "new york|brooklyn|seattle|bellevue|austin|boston|cambridge|chicago|denver|"
    "boulder|broomfield|louisville|los angeles|san diego|santa monica|irvine|"
    "portland|atlanta|miami|orlando|tampa|dallas|houston|philadelphia|"
    "pittsburgh|detroit|minneapolis|nashville|charlotte|raleigh|durham|"
    "washington dc|arlington|bethesda|salt lake city|phoenix|scottsdale|"
    "las vegas|kansas city|st. louis|columbus|cleveland|cincinnati|indianapolis|"
    "madison|milwaukee|sacramento|san rafael|pleasanton|walnut creek|"
    "north chicago|chattanooga|plano"
)
US_HINTS = re.compile(
    r"\b(united states|usa|u\.s\.|remote.{0,10}(us|usa|united states)|"
    + "|".join(US_STATES)
    + r")\b|" + US_CITIES + r"|remote",
    re.I,
)


def looks_us(location: str) -> bool:
    """True unless the location clearly points outside the US."""
    if not location:
        return True  # unknown -> let the scorer judge from the JD
    if NON_US_HINTS.search(location) and not re.search(
        r"\b(us|usa|united states)\b", location, re.I
    ):
        return False
    return bool(US_HINTS.search(location)) or "," in location

#!/usr/bin/env python3
"""Render resume.json to a single-page PDF via headless Chrome.

    python3 render.py                             # master resume
    python3 render.py --tailor stripe.json        # tailored variant
    python3 render.py --tailor stripe.json --out out/stripe.pdf

A tailor file is a small JSON overlay, never a full copy of the resume:

    {
      "label": "stripe-ai-pm",
      "tagline": "optional replacement headline",
      "summary": "optional short line under the header",
      "drop":  ["ec-homepage"],                # bullet ids to omit
      "order": ["ec-agent", ...],              # bullets first, then the rest
      "rewrite": {"ec-agent": "new text"},     # replace a bullet's wording
      "skills_first": ["Technical", "Product Management"],
      "skills_add":   {"Technical": ["Agentic AI"]},
      "hide_sections": ["links"]
    }

Rules the renderer enforces:
  - output is ALWAYS one page (it shrinks type/spacing until it fits, then
    reports the scale it settled on)
  - `rewrite` may only reword an existing bullet, never invent a new one
  - anything not mentioned in the tailor file falls through unchanged
"""
import argparse, json, pathlib, re, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
]


def find_chrome():
    for c in CHROME_CANDIDATES:
        if c and pathlib.Path(c).exists():
            return c
    sys.exit("No Chrome/Chromium found. Install Google Chrome.")


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------------------------------
# prose guard
# --------------------------------------------------------------------------
# Two standing style rules for anything written in the candidate's voice
# (no em-dashes, no strawman contrast). They are the LLM-tell phrasings that
# keep recurring, so they are checked mechanically. Edit _BANNED to taste.
_BANNED = [
    (re.compile(r"\u2014"), "em-dash (see the no-em-dashes rule)"),
    (re.compile(r"\brather than\b|\binstead of\b|\bnot just\b|\bmore than just\b"
                r"|\bbeyond simply\b|\bis ?n[o']t the hard part\b", re.I),
     "strawman contrast: defining the work against a worse alternative nobody proposed"),
]


def check_prose(tailor):
    """Warn loudly about phrasing the candidate has banned."""
    if not tailor:
        return
    prose = {"summary": tailor.get("summary", ""), "tagline": tailor.get("tagline", "")}
    prose.update(tailor.get("rewrite", {}))
    for field, text in prose.items():
        for pat, why in _BANNED:
            m = pat.search(text or "")
            if m:
                print(f"  !! {field}: {why}\n     -> {m.group(0)!r} in: {text[:110]}...",
                      file=sys.stderr)


# --------------------------------------------------------------------------
# tailoring
# --------------------------------------------------------------------------
def apply_tailor(data, t):
    if not t:
        return data
    check_prose(t)
    drop = set(t.get("drop", []))
    order = t.get("order", [])
    rewrite = t.get("rewrite", {})

    if t.get("tagline"):
        data["basics"]["tagline"] = t["tagline"]
    if t.get("summary"):
        data["basics"]["summary"] = t["summary"]

    known = {b["id"] for job in data["experience"] for b in job["bullets"]}
    for bad in set(rewrite) - known:
        print(f"  ! rewrite targets unknown bullet id {bad!r}, ignored", file=sys.stderr)
    for bad in drop - known:
        print(f"  ! drop targets unknown bullet id {bad!r}, ignored", file=sys.stderr)

    rank = {bid: i for i, bid in enumerate(order)}
    for job in data["experience"]:
        kept = [b for b in job["bullets"] if b["id"] not in drop]
        for b in kept:
            if b["id"] in rewrite:
                b["text"] = rewrite[b["id"]]
        kept.sort(key=lambda b: rank.get(b["id"], 10_000))
        job["bullets"] = kept
    data["experience"] = [j for j in data["experience"] if j["bullets"]]

    for cat, extra in t.get("skills_add", {}).items():
        for s in data["skills"]:
            if s["name"] == cat:
                s["items"] = list(dict.fromkeys(s["items"] + extra))
    first = t.get("skills_first", [])
    if first:
        srank = {n: i for i, n in enumerate(first)}
        data["skills"].sort(key=lambda s: srank.get(s["name"], 10_000))

    for sec in t.get("hide_sections", []):
        data[sec] = []
    return data


# --------------------------------------------------------------------------
# html
# --------------------------------------------------------------------------
ACCENT = "#c0142c"

CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: #fff; }
body {
  font-family: Carlito, Calibri, "Helvetica Neue", Helvetica, Arial, sans-serif;
  color: #1a1a1a; font-size: calc(9.3pt * var(--s));
  line-height: 1.35; -webkit-font-smoothing: antialiased;
}
/* No min-height: 297mm. That is the exact A4 sheet height, so sub-pixel
   rounding spills to a second page and the auto-fit shrinks type for nothing. */
.page { width: 210mm; }

header { background: #f4f4f4;
         padding: calc(7mm * var(--s)) calc(11mm * var(--s)) calc(5.5mm * var(--s));
         display: flex; justify-content: space-between; align-items: flex-start; gap: 7mm; }
h1 { font-family: Alegreya, Georgia, serif; color: ACCENT_COLOR;
     font-size: calc(25pt * var(--s)); font-weight: 700; line-height: 1;
     letter-spacing: .01em; text-transform: uppercase; }
.tagline { font-family: Alegreya, Georgia, serif; font-weight: 700;
           font-size: calc(10.2pt * var(--s)); line-height: 1.35;
           margin-top: calc(3.5pt * var(--s)); max-width: 118mm; }
.contact { font-family: Alegreya, Georgia, serif; font-weight: 700;
           font-size: calc(8.8pt * var(--s)); line-height: 1.5; white-space: nowrap; }
.contact a { color: ACCENT_COLOR; }

.body { display: flex; gap: calc(8mm * var(--s));
        padding: calc(5mm * var(--s)) calc(11mm * var(--s)) calc(8mm * var(--s)); }
.main { flex: 1 1 0; min-width: 0; }
.side { width: calc(58mm * var(--s)); flex: none; }

h2 { color: ACCENT_COLOR; font-size: calc(10.6pt * var(--s)); font-weight: 700;
     text-transform: uppercase; letter-spacing: .02em;
     margin-bottom: calc(4pt * var(--s)); }
.side h2 { margin-top: calc(9pt * var(--s)); }
.side h2:first-child, .main h2:first-child { margin-top: 0; }

.job { margin-bottom: calc(7pt * var(--s)); }
.job:last-child { margin-bottom: 0; }
.role { font-weight: 700; font-size: calc(9.8pt * var(--s)); }
.co { font-weight: 700; font-style: italic; font-size: calc(9.6pt * var(--s)); }
.dates { color: #333; font-size: calc(8.6pt * var(--s)); }
ul { list-style: none; margin-top: calc(3pt * var(--s)); }
li { position: relative; padding-left: calc(9pt * var(--s));
     margin-bottom: calc(2.4pt * var(--s)); }
li::before { content: "\\2022"; position: absolute; left: calc(1.5pt * var(--s));
             top: -.03em; color: #333; }

.edu { margin-bottom: calc(5pt * var(--s)); }
.edu .school { font-weight: 700; text-transform: uppercase; }
.edu .deg { font-weight: 700; }
.edu .note { font-weight: 700; font-style: italic; }
.edu .yrs { color: #333; }

.skill { margin-bottom: calc(5pt * var(--s)); }
.skill .cat { font-weight: 700; text-transform: uppercase; }

.link { margin-bottom: calc(5pt * var(--s)); }
.link a { color: ACCENT_COLOR; font-weight: 700; text-decoration: underline; }
""".replace("ACCENT_COLOR", ACCENT)


def build_html(d, scale):
    b = d["basics"]
    li = b.get("linkedin", "")
    li_href = li if li.startswith("http") else f"https://{li}"
    def link(v):
        if not v:
            return ""
        href = v if v.startswith("http") else f"https://{v}"
        shown = re.sub(r"^https?://(www\.)?", "", v).rstrip("/")
        return f'<a href="{esc(href)}">{esc(shown)}</a>'

    contact = "<br>".join(filter(None, [
        esc(b.get("location")),
        esc(b.get("email")),
        esc(b.get("phone")),
        link(li), link(b.get("github")), link(b.get("website")),
    ]))

    o = [f'<style>:root{{--s:{scale};}}{CSS}</style><div class="page">']
    o.append(
        f'<header><div><h1>{esc(b["name"])}</h1>'
        f'<div class="tagline">{esc(b.get("summary") or b.get("tagline",""))}</div></div>'
        f'<div class="contact">{contact}</div></header><div class="body">'
    )

    o.append('<div class="main">')
    if d.get("experience"):
        o.append("<h2>Work Experience</h2>")
        for j in d["experience"]:
            blurb = f': {esc(j["blurb"])}' if j.get("blurb") else ""
            o.append(
                f'<div class="job">'
                f'<div class="role">{esc(j["role"])}</div>'
                f'<div class="co">{esc(j["company"])}{blurb}</div>'
                f'<div class="dates">{esc(j["start"])} - {esc(j["end"])}</div><ul>'
                + "".join(f'<li>{esc(x["text"])}</li>' for x in j["bullets"])
                + "</ul></div>"
            )
    o.append("</div>")

    o.append('<div class="side">')
    if d.get("education"):
        o.append("<h2>Education</h2>")
        for e in d["education"]:
            note = f'<div class="note">{esc(e["note"])}</div>' if e.get("note") else ""
            o.append(
                f'<div class="edu"><div class="school">{esc(e["school"])}</div>'
                f'<div class="deg">{esc(e["degree"])}</div>{note}'
                f'<div class="yrs">{esc(e["start"])}-{esc(e["end"])}</div></div>'
            )
    if d.get("skills"):
        o.append("<h2>Skills</h2>")
        for s in d["skills"]:
            o.append(f'<div class="skill"><div class="cat">{esc(s["name"])}</div>'
                     f'<div>{esc(", ".join(s["items"]))}</div></div>')
    if d.get("links"):
        o.append("<h2>Links &amp; Mentions</h2>")
        for l in d["links"]:
            o.append(f'<div class="link"><a href="{esc(l["url"])}">{esc(l["title"])}</a>'
                     f'<div>{esc(l["text"])}</div></div>')
    o.append("</div></div></div>")
    return "".join(o)


# --------------------------------------------------------------------------
# pdf
# --------------------------------------------------------------------------
def page_count(pdf: pathlib.Path):
    raw = pdf.read_bytes()
    m = re.search(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", raw, re.S)
    if m:
        return int(m.group(1))
    return len(re.findall(rb"/Type\s*/Page[^s]", raw)) or 1


def to_pdf(chrome, html, dest):
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "r.html"
        src.write_text(html, encoding="utf-8")
        subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--no-sandbox",
             "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
             "--virtual-time-budget=6000",
             f"--print-to-pdf={dest}", src.as_uri()],
            check=True, capture_output=True, timeout=120,
        )
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE / "resume.json"))
    ap.add_argument("--tailor")
    ap.add_argument("--out")
    ap.add_argument("--keep-html", action="store_true")
    a = ap.parse_args()

    data = json.loads(pathlib.Path(a.data).read_text())
    tailor = json.loads(pathlib.Path(a.tailor).read_text()) if a.tailor else None
    data = apply_tailor(data, tailor)

    label = (tailor or {}).get("label") or "master"
    dest = pathlib.Path(a.out) if a.out else HERE / "out" / f"{label}.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)

    chrome = find_chrome()
    # Shrink until it fits one page. 1.0 first so a short resume stays full size.
    for scale in [1.0, .98, .96, .94, .92, .90, .88, .86, .84, .82, .80, .77, .74]:
        html = build_html(json.loads(json.dumps(data)), scale)
        to_pdf(chrome, html, str(dest))
        n = page_count(dest)
        if n == 1:
            if a.keep_html:
                (dest.with_suffix(".html")).write_text(html, encoding="utf-8")
            kb = dest.stat().st_size / 1024
            print(f"✓ {dest}  (1 page, scale {scale:g}, {kb:.0f} KB)")
            if scale < .84:
                print("  ! heavy shrink — consider dropping a bullet instead", file=sys.stderr)
            return
        print(f"  scale {scale:g} -> {n} pages, shrinking", file=sys.stderr)

    sys.exit("Could not fit one page even at minimum scale. Drop bullets in the tailor file.")


if __name__ == "__main__":
    main()

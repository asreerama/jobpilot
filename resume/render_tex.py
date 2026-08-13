#!/usr/bin/env python3
"""Render resume.json to a single-column, ATS-safe, single-page PDF via pdflatex.

    python3 render_tex.py
    python3 render_tex.py --tailor tailor/stripe-ai-pm.json
    python3 render_tex.py --tailor tailor/x.json --out out/x.pdf --keep-tex

Design constraints, all of them for ATS parsers rather than looks:
  - ONE column. Multi-column resumes are the single most common cause of
    scrambled ATS output: the parser reads across both columns and interleaves
    them. This is the actual reason to move off the two-column design.
  - No tabular layout, no text boxes, no graphics, no header/footer. Dates are
    right-aligned with \\hfill inside a normal paragraph, which extracts as
    "Role   Date" on one line.
  - Linux Libertine text with Biolinum small-caps headings, embedded Type1.
    No icon fonts: a FontAwesome glyph extracts as garbage or nothing.
  - Packages: geometry, hyperref, libertine, enumitem, microtype, xcolor.
  - Section headings are plain bold uppercase words that an ATS recognises:
    SUMMARY / EXPERIENCE / EDUCATION / SKILLS / PROJECTS.

Fit: pdflatex has no auto-fit, so this compiles, checks the page count with
pdfinfo, and steps the font size and spacing down until it lands on one page.
"""
import argparse, json, pathlib, re, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
from render import apply_tailor  # reuse the exact same overlay semantics


def find_pdflatex():
    for c in [shutil.which("pdflatex"),
              "/Library/TeX/texbin/pdflatex",
              "/usr/local/texlive/2026basic/bin/universal-darwin/pdflatex"]:
        if c and pathlib.Path(c).exists():
            return c
    sys.exit("pdflatex not found. Install with: brew install --cask basictex "
             "(then open a new shell, or use /Library/TeX/texbin/pdflatex)")


# --------------------------------------------------------------------------
# escaping
# --------------------------------------------------------------------------
UNI = {
    "–": "--", "—": "---", "’": "'", "‘": "`",
    "“": "``", "”": "''", "…": "...", " ": " ",
    "↔": r"$\leftrightarrow$", "→": r"$\rightarrow$",
    "•": r"$\bullet$", "▪": r"$\blacksquare$", "≥": r"$\geq$",
}
ESC = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
       "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
       "^": r"\textasciicircum{}", "\\": r"\textbackslash{}"}


def tex(s):
    if s is None:
        return ""
    s = str(s)
    for k, v in UNI.items():
        s = s.replace(k, v)
    out = []
    for ch in s:
        if ch in ESC and not (ch == "$" and False):
            out.append(ESC[ch])
        else:
            out.append(ch)
    r = "".join(out)
    # UNI replacements above intentionally emit math mode; un-escape those.
    for v in UNI.values():
        if v.startswith("$"):
            r = r.replace(tex_literal(v), v)
    return r


def tex_literal(v):
    """What tex() would have done to an already-LaTeX fragment."""
    out = []
    for ch in v:
        out.append(ESC.get(ch, ch))
    return "".join(out)


# --------------------------------------------------------------------------
# styles
# --------------------------------------------------------------------------
# Both are single column and ATS-safe. "sans" is the plain workhorse.
# "serif" is the classic LaTeX look: Linux Libertine text with Biolinum
# small-caps headings. Libertine ships real small caps, so headings get shape
# and rhythm without resorting to icon fonts or coloured boxes.
FONTBLOCK = r"""\usepackage{libertine}
\renewcommand{\familydefault}{\rmdefault}"""

# No \textls anywhere: microtype letterspacing fragments words in the extracted
# text layer ("E d u c at i o n"), inconsistently and invisibly to the eye.
SECMACRO = r"\newcommand{\SEC}[1]{\vspace{%(beforesec)spt}{\fontsize{%(sec)s}{%(sec)s}\selectfont\sffamily\bfseries\scshape #1}\par\secrule}"
NAMEMACRO = r"{\fontsize{%(name)s}{%(name)s}\selectfont\scshape %(nm)s}"

# --------------------------------------------------------------------------
# document
# --------------------------------------------------------------------------
PREAMBLE = r"""\documentclass{article}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[margin=%(margin)smm,top=%(top)smm,bottom=%(top)smm]{geometry}
%(fontblock)s
\usepackage{microtype}
\usepackage{enumitem}
\usepackage{xcolor}
\definecolor{linkblue}{HTML}{0B3D91}
\usepackage[colorlinks=true,urlcolor=linkblue,linkcolor=linkblue]{hyperref}
\urlstyle{same}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}
\raggedright
\raggedbottom
\hyphenpenalty=5000
\tolerance=2000
\emergencystretch=2em
\newcommand{\secrule}{\vspace{1.2pt}\hrule height 0.6pt\vspace{%(afterrule)spt}}
%(secmacro)s
\newlist{RB}{itemize}{1}
\setlist[RB]{label=\textbullet, leftmargin=10pt, labelsep=4pt, topsep=%(topsep)spt,
             itemsep=%(itemsep)spt, parsep=0pt, partopsep=0pt, rightmargin=0pt}
\begin{document}
\fontsize{%(base)s}{%(lead)s}\selectfont
"""


def build_tex(d, p):
    b = d["basics"]
    q = dict(p)
    q["fontblock"] = FONTBLOCK
    q["secmacro"] = SECMACRO % p
    o = [PREAMBLE % q]

    # ---- header ----
    o.append(r"\begin{center}" + (NAMEMACRO % {"name": p["name"],
             "nm": tex(b["name"]).upper()}) + r"\par\vspace{2.5pt}")
    bits = [tex(b.get("location")), tex(b.get("email")), tex(b.get("phone"))]
    li = b.get("linkedin", "")
    if li:
        short = re.sub(r"^https?://(www\.)?", "", li).rstrip("/")
        bits.append(r"\href{%s}{%s}" % (li if li.startswith("http") else "https://" + li, tex(short)))
    for key in ("github", "website"):
        v = b.get(key)
        if v:
            href = v if v.startswith("http") else "https://" + v
            bits.append(r"\href{%s}{%s}" % (href, tex(re.sub(r"^https?://(www\.)?", "", v).rstrip("/"))))
    o.append(r"{\fontsize{%s}{%s}\selectfont %s}\end{center}\vspace{%spt}"
             % (p["small"], p["small"], " $\\vert$ ".join(x for x in bits if x), p["afterhdr"]))

    # ---- summary ----
    summary = b.get("summary") or b.get("tagline")
    if summary:
        o.append(r"\SEC{Summary}" + tex(summary) + r"\par")

    # ---- experience ----
    if d.get("experience"):
        o.append(r"\SEC{Experience}")
        for i, j in enumerate(d["experience"]):
            if i:
                o.append(r"\vspace{%spt}" % p["jobgap"])
            o.append(r"{\bfseries %s} \hfill {\fontsize{%s}{%s}\selectfont %s -- %s}\par"
                     % (tex(j["role"]), p["small"], p["small"], tex(j["start"]), tex(j["end"])))
            line = r"{\itshape %s}" % tex(j["company"])
            if j.get("blurb"):
                line += r"{\itshape : %s}" % tex(j["blurb"])
            o.append(line + r"\par")
            if j["bullets"]:
                o.append(r"\begin{RB}")
                for x in j["bullets"]:
                    o.append(r"\item %s" % tex(x["text"]))
                o.append(r"\end{RB}")

    # ---- education ----
    if d.get("education"):
        o.append(r"\SEC{Education}")
        for i, e in enumerate(d["education"]):
            if i:
                o.append(r"\vspace{%spt}" % p["edugap"])
            o.append(r"{\bfseries %s} \hfill {\fontsize{%s}{%s}\selectfont %s--%s}\par"
                     % (tex(e["school"]), p["small"], p["small"], tex(e["start"]), tex(e["end"])))
            deg = tex(e["degree"])
            if e.get("note"):
                deg += ", " + tex(e["note"])
            o.append(deg + r"\par")

    # ---- skills ----
    if d.get("skills"):
        o.append(r"\SEC{Skills}\begin{RB}")
        for s in d["skills"]:
            o.append(r"\item {\bfseries %s:} %s" % (tex(s["name"]), tex(", ".join(s["items"]))))
        o.append(r"\end{RB}")

    # ---- projects ----
    if d.get("links"):
        o.append(r"\SEC{Projects \& Mentions}\begin{RB}")
        for l in d["links"]:
            url = re.sub(r"^https?://", "", l["url"])
            o.append(r"\item {\bfseries %s} -- %s \href{%s}{%s}"
                     % (tex(l["title"]), tex(l["text"]), l["url"], tex(url)))
        o.append(r"\end{RB}")

    o.append(r"\end{document}")
    return "\n".join(o)


# --------------------------------------------------------------------------
# compile
# --------------------------------------------------------------------------
def page_count(pdf):
    pdfinfo = shutil.which("pdfinfo") or "/opt/homebrew/bin/pdfinfo"
    if pathlib.Path(pdfinfo).exists():
        r = subprocess.run([pdfinfo, str(pdf)], capture_output=True, text=True)
        m = re.search(r"Pages:\s+(\d+)", r.stdout)
        if m:
            return int(m.group(1))
    raw = pathlib.Path(pdf).read_bytes()
    m = re.search(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", raw, re.S)
    return int(m.group(1)) if m else 1


def compile_tex(latex, src_tex, dest):
    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td) / "r.tex"
        t.write_text(src_tex, encoding="utf-8")
        for _ in range(2):                      # twice so \hfill settles
            r = subprocess.run(
                [latex, "-interaction=nonstopmode", "-halt-on-error",
                 "-output-directory", td, str(t)],
                capture_output=True, text=True, timeout=180)
        pdf = pathlib.Path(td) / "r.pdf"
        if not pdf.exists():
            log = (pathlib.Path(td) / "r.log")
            tail = log.read_text(errors="ignore")[-2500:] if log.exists() else r.stdout[-2500:]
            sys.exit("pdflatex failed:\n" + tail)
        shutil.copy(pdf, dest)
    return dest


# steps: (base, lead, name, sec, small, margin, top, itemsep, topsep, beforesec,
#         afterrule, jobgap, edugap, afterhdr)
def params(k):
    # Margins tighten faster than type. Packing the page is the standard way to
    # keep one page without dropping content, and 9mm still prints safely.
    base = round(10.0 - k * 0.26, 2)
    # Floor at 12.7mm (0.5in). Tighter than this and the line length runs
    # nearly the full sheet width, which reads as a stretched, horizontal
    # page even though the orientation is correct portrait.
    marg = round(max(12.7, 16.5 - k * 1.1), 2)
    return {
        "base": base, "lead": round(base * 1.18, 2),
        "name": round(base * 1.85, 2), "sec": round(base * 1.03, 2),
        "small": round(base * 0.92, 2),
        "margin": marg, "top": round(max(10.0, marg - 2.5), 2),
        "itemsep": round(max(0.8, 1.9 - k * 0.18), 2),
        "topsep": round(max(1.0, 2.4 - k * 0.2), 2),
        "beforesec": round(max(4.0, 8.5 - k * 0.65), 2),
        "afterrule": round(max(2.0, 4.0 - k * 0.3), 2),
        "jobgap": round(max(2.6, 5.0 - k * 0.42), 2),
        "edugap": round(max(1.6, 3.0 - k * 0.25), 2),
        "afterhdr": round(max(1.0, 2.0 - k * 0.18), 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE / "resume.json"))
    ap.add_argument("--tailor")
    ap.add_argument("--out")
    ap.add_argument("--keep-tex", action="store_true")
    a = ap.parse_args()

    data = json.loads(pathlib.Path(a.data).read_text())
    t = json.loads(pathlib.Path(a.tailor).read_text()) if a.tailor else None
    data = apply_tailor(data, t)

    label = (t or {}).get("label") or "master"
    dest = pathlib.Path(a.out) if a.out else HERE / "out" / f"{label}-ats.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    latex = find_pdflatex()

    for k in range(0, 9):
        p = params(k)
        src = build_tex(json.loads(json.dumps(data)), p)
        compile_tex(latex, src, str(dest))
        n = page_count(dest)
        if n == 1:
            if a.keep_tex:
                dest.with_suffix(".tex").write_text(src, encoding="utf-8")
            print(f"✓ {dest}  (1 page, {p['base']}pt, margin {p['margin']}mm, "
                  f"{dest.stat().st_size/1024:.0f} KB)")
            if p["base"] < 8.6:
                print("  ! type is getting small; drop a bullet instead", file=sys.stderr)
            return
        print(f"  {p['base']}pt -> {n} pages, tightening", file=sys.stderr)

    sys.exit("Could not fit one page. Drop bullets in the tailor file.")


if __name__ == "__main__":
    main()

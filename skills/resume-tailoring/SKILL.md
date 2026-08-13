---
name: resume-tailoring
description: Produce a tailored, single-page, ATS-safe PDF resume for a specific job from the JobPilot resume/resume.json. Use when asked to tailor/customize a resume, generate a resume PDF for a posting, or when applying to a job. Renders via pdflatex (default) or headless Chrome, auto-fits to exactly one page, and never invents experience.
---

# Tailored Resume PDFs

Location: `resume/` inside the JobPilot repo.

```
resume.json                # master data, the single source of truth
render_tex.py              # LaTeX renderer (DEFAULT: single column, ATS-safe)
render.py                  # Chrome renderer (two-column, styled; for humans)
tailor/<label>.json        # one small overlay per job
out/<label>-ats.pdf        # generated output
```

## Use the LaTeX renderer by default

`render_tex.py` (pdflatex, single column) is **the default**. Multi-column
resumes are the single most common cause of scrambled ATS parses.

```bash
export PATH="/Library/TeX/texbin:$PATH"
python3 render_tex.py                                    # master
python3 render_tex.py --tailor tailor/<label>.json       # tailored
python3 render_tex.py --tailor tailor/x.json --keep-tex  # also write .tex
```

**Always verify the parse, not just the picture:**

```bash
pdftotext -layout out/<label>-ats.pdf - | head -30
```

Reading order must be linear top to bottom with nothing interleaved.

To see the result, rasterize and Read the PNG (needs poppler:
`brew install poppler`):

```bash
pdftoppm -png -r 130 out/<label>-ats.pdf out/check   # -> out/check-1.png
```

**Always look at the PNG before sending the PDF anywhere.**

## The tailor overlay

Never copy the whole resume. An overlay only says what changes
(see `tailor/example.json`):

```json
{
  "label": "acme-ai-pm",
  "_job": "URL of the posting",
  "_why": "one line on what the JD actually rewards",
  "summary": "replacement headline under the name",
  "drop": ["bullet-id"],
  "order": ["bullet-id", "..."],
  "rewrite": {"bullet-id": "reworded text"},
  "skills_first": ["Technical", "Product"],
  "skills_add": {"Technical": ["Agentic AI"]},
  "hide_sections": ["links"]
}
```

`drop` and `order` do most of the work. `order` floats the listed bullets to
the top of their own job; unlisted ones keep their relative order below.
Bullet ids live in `resume.json`. Unknown ids warn instead of failing
silently.

## Tailoring: rewrite, do not just reorder

Dropping and reordering is the floor, not the job. **Rewrite the three to six
bullets the JD actually turns on**, via `rewrite`. Rewriting everything every
time is churn.

### Hard constraints

1. **Facts never change.** Every number, customer count, company, and
   technology must survive. `rewrite` reframes emphasis and leading verb. It
   never invents a responsibility, metric, or tool. If the JD wants something
   the candidate has not done, the answer is to not claim it.
2. **Length must not grow.** The page budget is fixed, so a longer bullet
   costs a line and forces the font down. Aim for the same length or shorter.
3. **Voice rules apply** to every `summary`, `tagline`, and `rewrite`: the
   defaults below plus anything in `profile.md`. The renderer runs a
   mechanical `check_prose()` guard for the first two (edit `_BANNED` in
   `render.py` to extend it). **If you see a `!!` line in the output, fix
   the text and re-render.**
   - No em-dashes. Restructure the sentence instead.
   - No strawman contrast: never define the work against a worse
     alternative nobody proposed ("rather than", "instead of", "not just",
     "more than just", "beyond simply", "X isn't the hard part"). Contrast
     against a real prior state is fine.
   - No explainer clauses bolted onto sentences ("which means...",
     "...that tells you..."). State the fact and stop.
   - The delete test: remove the trailing clause; if no fact is lost, cut
     it. These are the standard tells of AI-generated text, and a resume
     that trips them reads as machine-written.
4. Keep it **ATS-safe**: real text, one column, no images, no text in
   headers/footers.

## Watch the size it settles on

`render_tex.py` prints e.g. `1 page, 9.22pt, margin 9.1mm`. Margins tighten
before type does, on purpose. Below ~8.6pt the type is getting cramped, so
drop a bullet rather than shrink further. A good tailored resume lands
9.0-9.7pt. (The Chrome renderer reports a `scale` instead; 0.86-0.95 is its
good range.)

## Toolchain

- pdflatex: `brew install --cask basictex`, then
  `sudo tlmgr install collection-fontsrecommended enumitem titlesec microtype`.
  BasicTeX alone ships `helvet.sty` but not its Type1 fonts, which fails with
  `I can't find file 'phvr8t'`; fontsrecommended fixes it.
- poppler (`pdftotext`, `pdftoppm`, `pdfinfo`): `brew install poppler`.
- The Chrome renderer needs any Chrome/Chromium install, nothing else.

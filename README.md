<div align="center">

# Z-Link

**Plain-text citations → real, linked Zotero citations.**

Resolves every reference in a manuscript to the *correct* paper, creates the Zotero items,
and returns a `.docx` ready for ODF Scan.

<br>

![runs in browser](https://img.shields.io/badge/browser-no%20install-1f7a3f?style=flat-square)
![python](https://img.shields.io/badge/CLI-Python%203.11+-2c5282?style=flat-square)
![accuracy](https://img.shields.io/badge/measured-82%2F82%20references-1f7a3f?style=flat-square)
![parity](https://img.shields.io/badge/engine%20parity-47%2F47%20mutants%20killed-1f7a3f?style=flat-square)
![deps](https://img.shields.io/badge/web%20build-zero%20dependencies-8a6a20?style=flat-square)

<br>

[Quick start](#quick-start) · [How matching works](#how-matching-works) ·
[Zotero setup](#zotero-setup) · [Verification](#verifying-the-browser-port) ·
[Options](#all-options)

</div>

---

## What it does

You write. It handles everything after.

```
manuscript.docx                         manuscript_scannable.docx
  "…widely.¹  …disagree.²⁻⁴"     ──▶      "…widely.{ | Barro, (1992) | | |zu:…}"
  1. Barro RJ… J Polit Econ…             + 47 items created in your Zotero library
  2. Marmot M… Lancet…                   + report.csv explaining every decision
```

It does **not** decide *where* a citation belongs — that is your judgment while writing.
It automates finding each paper, verifying it is actually the right one, getting it into
Zotero, and stamping the marker.

Anything it cannot confirm becomes a visible `{NEEDS REVIEW: n}` in the document rather
than a live citation quietly pointing at the wrong paper.

### Measured on real manuscripts

| Manuscript | Refs | Resolved | In-text markers |
|:--|--:|--:|--:|
| Convergence in disease burden — epidemiology, economics, books | 35 | **35 / 35** | 47 |
| Osteoporosis burden in India — Lancet house style | 47 | **47 / 47** | 23 groups |

Neither needed a manual review pass. The second surfaced four genuinely broken
bibliography entries the gate refused to guess at — see [Advisories](#advisories).

---

## Two front ends, one engine

<table>
<tr><th></th><th>Z-Link — browser</th><th>zotprep — command line</th></tr>
<tr><td><b>Install</b></td><td>none</td><td><code>pip install -r requirements.txt</code></td></tr>
<tr><td><b>Runs</b></td><td>entirely in your browser</td><td>on your machine</td></tr>
<tr><td><b>Writes to Zotero</b></td><td>always</td><td>only with <code>--live</code></td></tr>
<tr><td><b>Preview without writing</b></td><td>—</td><td><code>--dry-run</code>, the default</td></tr>
<tr><td><b>Review unresolved</b></td><td>click through candidates</td><td><code>--review</code> prompt</td></tr>
<tr><td><b>Remembers decisions</b></td><td>for the session</td><td>forever, in SQLite</td></tr>
<tr><td><b>Separate bibliography file</b></td><td>—</td><td><code>--bibliography</code></td></tr>
</table>

The browser build is a **verified port** of the Python engine, not a reimplementation —
see [Verifying the browser port](#verifying-the-browser-port).

---

## Quick start

### Browser

Open the page, drop in a `.docx`, done. The **First time here** button walks through
getting a Zotero key and installing the ODF Scan plugin, which downloads from the app
itself.

> **Your manuscript never leaves the tab.** There is no server and no upload. The page
> talks to Crossref, OpenAlex, Europe PMC, PubMed, Semantic Scholar and the Zotero API
> directly from your browser — all six permit it with `Access-Control-Allow-Origin: *`.
> It loads **no third-party script at all**; even `.docx` unzipping uses the browser's own
> Compression Streams. There is nothing on the page that *could* send your work anywhere.

Your userID and API key stay in that browser's local storage and are sent only to
`api.zotero.org`, in a request header rather than a web address.

### Command line

```bash
pip install -r requirements.txt
```

Save your credentials once — they go to `~/.zotprep/config.json`, created `0600`:

```bash
python -m zotprep --zotero-userid 1234567 --zotero-key YOUR_KEY \
                  --mailto you@email.com --save-credentials
```

Every run after that is just the manuscript. **Dry run is the default** — nothing reaches
your library without `--live`:

```bash
python -m zotprep --manuscript yourpaper.docx          # resolve and report
python -m zotprep --manuscript yourpaper.docx --live   # ...and create the items
```

Re-running `--live` is safe: items are matched by DOI and by title+year against your
existing library and **reused, not duplicated**.

<details>
<summary><b>Credential precedence, and keeping keys out of your shell history</b></summary>

<br>

Settings resolve highest-first — so a stored credential never silently overrides one you
asked for:

1. an explicit `--zotero-userid` / `--zotero-key` / `--mailto`
2. `$ZOTERO_USERID` / `$ZOTERO_KEY` / `$ZOTPREP_MAILTO`
3. the saved file

```bash
python -m zotprep --show-config          # where each value comes from, key masked
python -m zotprep --forget-credentials   # delete the saved file
```

`$ZOTPREP_CONFIG` overrides the file location, which is what the tests use so they never
touch a real one.

**Avoid putting a key inline on a command line.** It lands in shell history — and on this
project it also landed in a tool's approved-commands log, which is exactly where a live
key should not sit.

</details>

---

## Zotero setup

<details open>
<summary><b>1 · Get your userID and API key</b></summary>

<br>

At **https://www.zotero.org/settings/keys**:

1. Your **userID** is the number in *"Your userID for use in API calls is …"*. Not your
   username.
2. **Create new private key** → tick **Allow library access** *and* **Allow write
   access**. Without write access every run stops with an opaque `403`. This is the step
   people miss.
3. The key is shown **once**. Copy it now; if you lose it, delete that key and make
   another.

Revoke any key from the same page — it stops working immediately.

</details>

<details>
<summary><b>2 · Install the ODF Scan plugin</b> — one time, needs Zotero 7.0+</summary>

<br>

The `.xpi` is bundled at [`web/vendor/`](web/vendor/) and downloadable from Z-Link's help
panel, or from the [upstream releases page](https://github.com/Juris-M/zotero-odf-scan-plugin/releases).

1. Save the `.xpi` to disk. If your browser offers to *install* it and then calls it
   corrupt, it has mistaken it for a browser extension — right-click → **Save link as…**
2. Zotero → **Tools → Plugins** (Add-ons on older versions)
3. Gear icon ⚙ → **Install Plugin From File…** → pick the `.xpi` → restart if asked

</details>

<details>
<summary><b>3 · Run the scan</b></summary>

<br>

1. Zotero → **Tools → ODF Scan**
2. File type → **ODF (to citations)** — the direction that turns markers into live citations
3. **Input File** → `zot_out/manuscript_scannable.docx`
4. **Output File** → somewhere else, so you keep the original
5. **Next** → **Finish**

Open the result in your word processor → Zotero tab → Document Preferences → pick a style
→ Add/Edit Bibliography.

</details>

---

## Input requirements

The manuscript `.docx` needs:

1. Body text with placeholder citations — see [Citation notation](#citation-notation)
2. A heading reading `References`, `Bibliography`, `Works Cited` or `Reference List`,
   followed by the numbered list

A `Tables` / `Figures` / `Appendix` heading after the list ends the bibliography, so
captions are never imported as references. CLI only: `--bibliography refs.txt` if the
references live in a separate file.

Entry numbering may use `1.`, `1)`, `[1]`, or a bare number followed by whitespace —
including the **em space** (U+2003) that Lancet-family templates emit with no delimiter at
all.

<details>
<summary><b>Reference formats handled</b></summary>

<br>

```
Barro RJ, Sala-i-Martin X. Convergence. J Polit Econ. 1992;100(2):223-51.
Cauley JA. Public health impact of osteoporosis. J Gerontol A 2013; 68(10): 1243-51.
GBD 2021 Diseases and Injuries Collaborators. Global incidence... Lancet. 2024.
Theil H. Economics and Information Theory. Amsterdam: North-Holland; 1967.
Theil H. Economics and information theory. Amsterdam: North-Holland, 1967.
Cowell FA. Measuring Inequality. 3rd ed. Oxford: Oxford University Press; 2011.
Khosla S, Pacifici R. Estrogen deficiency... In: Marcus R, Dempster DW, et al, eds.
    Osteoporosis, 4th edn. Amsterdam: Elsevier, 2013: 1113-36.
```

That last form becomes a Zotero **bookSection** with the chapter title, containing
`bookTitle`, edition, publisher, place and page range.

</details>

---

## Citation notation

In-text citations are located in **paragraph coordinates, not per run**, because Word
freely splits one citation across runs — `statement;²` + `⁰` is a single citation no
per-run scan can see.

| Notation | Example | Means |
|:--|:--|:--|
| Bracketed | `[1]` `(2,3)` `[4-6]` | 1 / 2,3 / 4,5,6 |
| Word superscript formatting | a run styled superscript | its digits |
| Unicode superscript | `²³` | 23 |
| Lancet separator — U+00B7 middle dot | `¹·²⁻⁴` | 1, 2, 3, 4 |
| Superscript minus range — U+207B | `⁴³⁻⁴⁵` | 43, 44, 45 |
| Digits fused to punctuation | `systems.29-32` | 29…32 |

<details>
<summary><b>Superscripts that are <i>not</i> citations</b></summary>

<br>

Scientific manuscripts are full of superscripts that must never become citations. Writing
a Zotero marker into the middle of a number would corrupt your results section:

```
-13.3×10⁻³      exponent, not reference 3
1.60×10⁻⁴       exponent, not reference 4
R²=0.32         a square, not reference 2
R² increased    likewise
```

Rejected when the superscript is preceded by a digit, by `×`, or by another superscript
character (so `10⁻³` cannot match on its `³` alone); when followed by `=`; or when the
preceding token is a lone-letter variable.

In Lancet house style the middle dot is *also* the decimal separator (`39·1%`), which is
why the superscript pattern must begin **and** end on a superscript digit — a decimal
point sits between ASCII digits and cannot match.

</details>

<details>
<summary><b>Implausible ranges are flagged, not expanded</b></summary>

<br>

A superscript range spanning dozens of references is a dropped digit, not a range. Both of
these were real:

```
systems.³⁻³²      reads as refs 3-32   (30 refs) — meant ³⁰⁻³² or ³¹⁻³²
rates.¹¹⁻⁴²       reads as refs 11-42  (32 refs) — meant ⁴¹⁻⁴² or ¹¹·⁴²
```

Anything wider than 8 references is reported and the document text **left untouched**,
rather than manufacturing citations the author never made. Ranges whose endpoints are not
reference numbers at all (`(1990-2023)`, value spans) are ignored silently.

</details>

---

## How matching works

> **The principle:** find the correct paper first, *then* retrieve metadata — and accept a
> match only when several independent facts agree.

### Resolution order — cheapest and most certain first

```
1  correction store   ─ you already decided this one          → instant
2  identifier in text ─ DOI / PMID / PMCID present            → direct fetch, no search
3  resolution cache   ─ resolved in a previous run            → instant
4  provider fan-out   ─ 5 sources, concurrently, by title     → candidates
5  accept gate        ─ hard conjunctions                     → accept or review
6  build from text    ─ nothing indexes it, reference is full → FROM_TEXT
```

### The accept gate

Acceptance is a **conjunction of independent signals**, never a weighted score crossing a
threshold. A wrong paper can fake one signal; it cannot simultaneously match the title,
the year, the volume *and* the first page.

| Tier | Requirement | Confidence |
|:--|:--|--:|
| `identifier` | DOI/PMID/PMCID in the reference, resolved directly | 1.00 |
| `ecitmatch` | NCBI citation matcher hit **and** title ≥ 0.92 | 0.99 |
| `fingerprint` | title ≥ 0.90 **and** year **and** exact volume **and** exact first page | 0.97 |
| `title+journal+author` | title ≥ 0.93 **and** year **and** journal **and** author | 0.93 |
| `provider-agreement` | 2+ providers returned the same DOI, title ≥ 0.88 | 0.90 |
| `exact-title` | normalized titles identical **and** year **and** author | 0.88 |
| `from-reference-text` | no index holds it; item built from the reference itself | 0.80 |

Anything failing every tier becomes `{NEEDS REVIEW: ref n}` and is **not** created in
Zotero.

<details>
<summary><b>Why <code>ecitmatch</code> matters — and where it lies</b></summary>

<br>

NCBI's citation matcher takes `journal|year|volume|first_page|author` and returns an exact
PMID. It is a **lookup, not a search** — no relevance ranking, nothing to score. Vancouver
references already carry all five fields, so most biomedical references resolve
deterministically. It also works with the **author field blank**, which rescues consortium
references (`GBD 2021 Collaborators`) whose "first author" is not a personal surname.

But it does **not** reliably return `NOT_FOUND` when the citation is off. For journals that
number articles rather than pages (`J Clin Med` 4923, `Injury` 111528) and for supplements
(`16(Suppl 2):S233`) it can return a confidently wrong PMID for an unrelated paper. Hence
the title guard.

The guard is **0.92**, chosen from measurement rather than taste: across the two
manuscripts, 42 of 43 genuine hits scored ≥ 0.98, while a reference whose volume/pages had
been copied from a sibling paper in the same issue scored 0.83.

</details>

<details>
<summary><b>Journal abbreviation matching, without a lookup table</b></summary>

<br>

NLM-style abbreviations have a useful property: every abbreviated token is a prefix of the
corresponding full word, in order. Tokens are paired positionally and prefix-checked:

```
J Polit Econ            ≡ Journal of Political Economy
J R Stat Soc Series B   ≡ Journal of the Royal Statistical Society: Series B
Bull World Health Organ ≡ Bulletin of the World Health Organization
```

Token counts must match exactly, which is what makes `Lancet` ≠ `Lancet Oncology`.
Verified 21/21 correct and 7/7 near-misses rejected.

</details>

<details>
<summary><b>A known limit of title similarity — and why it is left alone</b></summary>

<br>

`token_set_ratio` scores **1.00** whenever one title's tokens are a subset of the other's,
so a short reference title matches a longer unrelated paper perfectly:

```
ref : "Renal osteodystrophy and chronic kidney disease-mineral bone disorder"
cand: "Mitochondrial dysfunction and mitophagy blockade contribute to renal
       osteodystrophy in chronic kidney disease-mineral bone disorder"
```

Damping the score by token-count ratio was tried and **reverted**: legitimate matches are
also subsets, because journals expand titles in the version of record. The GBD 2021
reference is 24 tokens in a bibliography and 45 in Crossref once `(YLDs)`, `(DALYs)`,
`811 subnational locations` and the trailing `a systematic analysis for the Global Burden
of Disease Study 2021` are included. Any penalty strong enough to reject the unrelated
subset also rejects that.

So the inflation is left alone and handled where it belongs: the gate never takes title
similarity on its own. The case above is rejected because its journal *and* authors both
disagree — a conjunction no string metric has to resolve.

</details>

---

## Advisories

Some references are internally inconsistent. These are reported after the run and in the
`advisory` column of `report.csv`. They are **advisories, not blockers** — the title is
usually what the author meant, and the correct paper is generally found anyway.

**Locator points at a different paper** — volume/pages copied from a neighbouring entry:

> `[13]` reference's journal/volume/pages (Lancet Glob Health 2018;6:e1363) point to a
> DIFFERENT paper than its title — the volume/pages were probably copied from a
> neighbouring reference.

**Title matches a real paper by other authors in another journal.** A genuine miscitation
usually gets the authors right and fumbles the volume. A title attached to the wrong
authors *and* the wrong journal is characteristic of a fabricated entry — and the named
authors are typically real researchers in the field, which is exactly what makes it
invisible on a read-through:

> `[32]` VERIFY THIS ENTRY: the title matches a real paper
> (10.1016/j.archger.2024.105519, Archives of Gerontology and Geriatrics 2024) whose
> authors (Harvey, Payne, Tan) and journal differ entirely from this reference (Sheehan,
> Injury).

To avoid crying wolf, this one additionally requires comparable title lengths (short titles
like *Economic Growth* are subsets of countless papers) and no loose surname agreement
(`Mendez` vs `Mendez-Guerra` is not a discrepancy). Advisories are suppressed for
references a real tier corroborated.

---

## Search providers

| Provider | Key | Cost | Role |
|:--|:-:|:--|:--|
| **Crossref** | — | free | Primary. All DOI'd literature including economics and books. Metadata source of record once a DOI is known. |
| **Europe PMC** | — | free | Biomedical workhorse. Returns `medlineAbbreviation` — the exact style Vancouver uses — plus volume/issue/pages. |
| **PubMed** | — | free | `ecitmatch` deterministic lookup, plus `[Title]`-field search. |
| **OpenAlex** | — | **metered** | Supplementary. Broad coverage including books. |
| **Semantic Scholar** | required | free with key | **Off by default.** |

<details>
<summary><b>Provider caveats, all learned the hard way</b></summary>

<br>

- **OpenAlex is now metered.** Unauthenticated callers get a small daily budget (~100
  requests, resets midnight UTC) then return `429 "Insufficient budget"`. One 35-reference
  run can exhaust it. That specific response **disables the provider for the rest of the
  run** instead of retrying. Accuracy does not depend on it.
- **Semantic Scholar 429s on nearly every unauthenticated call**, and its required ~1.1 s
  pacing serializes the whole run for a provider returning nothing. Wired up but off until
  you supply a free key.
- **Crossref rejects the entire request over one bad `select` field.** A single unsupported
  field name returns HTTP 400 for *every* reference — silently zeroing the provider while
  the run still "succeeds" with fewer votes. 4xx now fails fast and prints loudly rather
  than retrying four times.
- **Europe PMC returns zero hits, not an error, for a malformed query.** Its query language
  treats `: " ( ) [ ] { } ~ ^ ? *` and bare `AND`/`OR`/`NOT` as syntax, so titles are
  sanitized first.

A `search/google.py` provider can be dropped into `zotprep/search/` and added to the
fan-out without changing anything else. It was never needed to reach 100%.

</details>

---

## The last mile — deciding the ambiguous ones

```bash
python -m zotprep --manuscript yourpaper.docx --review
```

For anything that did not pass the gate, prints the top candidates with title, authors,
journal, year, volume/pages, DOI and which providers found each:

```
choose 1-5 / [s]kip / [d]oi / [b]uild from reference text:
```

Z-Link shows the same candidates as cards after a run, with the same four outcomes:

| Outcome | Result |
|:--|:--|
| pick a candidate | `ACCEPTED`, tier `manual`, confidence 1.0 |
| paste a DOI | `ACCEPTED`, tier `manual`, confidence 1.0 |
| build from the reference text | `FROM_TEXT`, tier `book-from-text`, confidence 0.80 |
| leave flagged | unchanged — stays `{NEEDS REVIEW: n}` |

Building from the reference text is offered only for book-shaped references. A pasted DOI
still goes through Crossref enrichment, so the item is built from the canonical record
rather than the bare identifier — you supply the DOI, not the metadata.

**CLI decisions are permanent.** Every one is written to the `corrections` table, so the
same reference never asks twice, in this manuscript or any future one. Web decisions last
for the session only.

---

## Outputs

Written to `zot_out/` (or `--outdir`); downloaded directly in the browser.

| File | Contents |
|:--|:--|
| `manuscript_scannable.docx` | Your document with Scannable Cite markers `{ \| Author, (Year) \| \| \|zu:USERID:ITEMKEY}` replacing the placeholder numbers, old bibliography removed |
| `report.csv` | `n, status, tier, confidence, doi, pmid, zotero_key, resolved_title, reason, advisory, raw_reference` |

> **Read `report.csv` before scanning.** `tier` tells you *why* each match was accepted,
> which is more informative than the confidence number. Rows with tier
> `from-reference-text` had no external corroboration — the metadata came from your own
> bibliography, so it is only as good as what you typed.

---

## Caching

`database/cache.sqlite`, two tables — CLI only:

| Table | Contents | Safe to delete? |
|:--|:--|:--|
| `resolutions` | memoized API results, keyed on normalized reference text | yes, only costs time |
| `corrections` | your `--review` decisions | **no** — you would re-answer everything |

Failures are never cached, since provider indexes improve over time. Because the key is the
*normalized* reference text, renumbering your bibliography or changing whitespace, dashes
or case still hits the cache. Disable with `--no-cache`.

---

## All options

| Flag | Required | Meaning |
|:--|:-:|:--|
| `--manuscript FILE` | ✔ | `.docx` input |
| `--bibliography FILE` | | Separate reference-list file |
| `--outdir DIR` | | Output folder, default `zot_out` |
| `--mailto EMAIL` | | Crossref/NCBI polite pool. Also `$ZOTPREP_MAILTO`, or saved |
| `--dry-run` | | Resolve and report, create nothing. **Default** |
| `--live` | to write | Create/reuse items. Must be explicit — never implied |
| `--zotero-userid ID` | with `--live` | Numeric userID. Also `$ZOTERO_USERID`, or saved |
| `--zotero-key KEY` | with `--live` | Read/write API key. Also `$ZOTERO_KEY`, or saved |
| `--review` | | Prompt for anything not auto-accepted |
| `--workers N` | | Concurrent references, default 12 |
| `--no-cache` | | Ignore and do not write the SQLite cache |
| `--save-credentials` | | Write userID/key/mailto to `~/.zotprep/config.json` |
| `--forget-credentials` | | Delete that file |
| `--show-config` | | Print where each setting comes from, key masked |

---

## Verifying the browser port

Rewriting a verified engine in another language risks silent behaviour drift. That risk is
**measured**, not argued away:

```bash
python web/tools/parity/run_all.py
```

Two phases, and both matter.

**① Parity** compares each ported module against its Python original over a corpus and
requires the *identical IEEE-754 double* — not a value within a tolerance. The accept gates
are hard thresholds (0.88 / 0.90 / 0.92 / 0.93), so a last-bit difference is a behaviour
difference.

**② Mutation** injects known port mistakes one at a time and requires each to be caught.

> Parity passing alone proves nothing. **Three times during this port it reported a clean
> pass for code that was provably wrong**, because the corpus never reached the relevant
> branch. Current score: **47/47 killable mutants killed**, with 6 documented as
> equivalent — each carrying the argument for why no test can kill it.

<details>
<summary><b>The language differences that produced real bugs</b></summary>

<br>

All five were caught by the harnesses, in my own code:

| | Python | JavaScript |
|:--|:--|:--|
| `rapidfuzz` internals | `(1 - d/l) * 100` in `ratio` | `100 - 100*d/l` in `token_set_ratio` — equal algebraically, different doubles |
| `len()`, `sorted()` | code points | UTF-16 code units |
| `\w`, `\b`, `isupper()` | Unicode-aware | ASCII only |
| `str.replace` | every occurrence | the first one |
| `format(x, ".2f")` | half to even | `toFixed` — half away from zero |

</details>

The `.docx` layer needs a DOM, so it has its own check against a fixture built by the
Python original:

```bash
python web/tools/parity/docx_fixture.py
python -m http.server 8731 --directory web
# open http://localhost:8731/tools/parity/docx_check.html
```

**End to end**, the same document through `python -m zotprep --dry-run --no-cache` and
through the browser produces the same status, tier, confidence, DOI and title for every
reference, the same rewritten-citation count, the same unresolved list, and the same
advisory strings character for character.

See [`web/README.md`](web/README.md) for the layout and how to add cases.

---

## Module layout

```
zotprep/                    the engine and the CLI
  cli.py                arg parsing, end-to-end flow
  config.py             saved credentials, flag/env/file precedence
  extractor.py          reference string → ParsedRef
  models.py             ParsedRef, Candidate, Resolution
  resolver.py           orchestration, from-text fallback, advisories
  scorer.py             signals + the accept gate
  utils.py              normalization, title similarity, journal matching
  cache.py              SQLite resolutions + corrections
  review.py             interactive confirm loop
  docx_writer.py        bibliography parsing, citation spans, docx mutation
  search/               base + crossref, europepmc, pubmed, openalex, semanticscholar
  zotero/client.py      item building, duplicate detection, creation

web/                        Z-Link — the browser build
  index.html            the app, including the first-run help
  app.js                UI wiring; credentials ↔ localStorage
  src/                  the port, one file per zotprep module it mirrors,
                        plus zip.js (Compression Streams) and pipeline.js
  vendor/               the ODF Scan plugin .xpi, with its NOTICE
  tools/parity/         differential tests and the mutation check

.github/workflows/pages.yml   runs parity, then deploys web/ to Pages
tests/                        refs_35.json fixture + accuracy harness
```

---

## Deploying

`.github/workflows/pages.yml` runs the parity suite on every push and publishes to GitHub
Pages **only if it passes**, so a drifting engine cannot ship. It stages `index.html`,
`app.js`, `src/` and `vendor/`; the parity harnesses stay out, being development tooling
that depends on fixtures which are deliberately not committed.

Enable once under **Settings → Pages → Source: GitHub Actions**. The repository must be
public for Pages on a free account.

---

## Known limitations

- Only body paragraphs are scanned. Citations inside **tables, footnotes, text boxes or
  endnotes are not** rewritten.
- `from-reference-text` items are only as accurate as your typed bibliography. Nothing
  external verified them. `complete_enough_for_text_item()` refuses to build one from a
  reference it could not fully parse — those go to review.
- Books rarely have a locator to fingerprint against, so they lean on `exact-title` or the
  from-text path.
- Metadata enrichment re-fetches the canonical Crossref record once a DOI is known, which
  improves creator names but drops the PMID from most items. DOI is the identifier that
  matters for citation styles.
- The parser is tuned for Vancouver and Lancet house style. Other formats may need one
  `--review` pass, after which they are cached forever.
- Z-Link needs Compression Streams — Chrome/Edge 80+, Firefox 113+, Safari 16.4+. It says
  so up front rather than failing later.
- Z-Link has no dry run and no correction store. Use the CLI when you want a preview pass
  or decisions that persist.

---

<details>
<summary><b>Predecessor — <code>zotprep.py</code> (v3, single file)</b></summary>

<br>

Still in the repo. It used Crossref's `query.bibliographic` on the whole reference string
plus a single fuzzy ratio against a reconstructed citation. That ratio measures shared
*boilerplate* — journal abbreviation, volume, page range — rather than paper identity, so
it sat in the 0.35–0.55 band for correct and incorrect matches alike, and a 0.55 threshold
admitted wrong papers. It scored roughly 20/35.

Kept for reference only. Use `python -m zotprep`.

</details>

---

<div align="center">
<sub>

Bundles [ODF Scan for Zotero](https://github.com/Juris-M/zotero-odf-scan-plugin) by
Sebastian Karcher and Frank Bennett — AGPL-3.0-or-later, see
[`web/vendor/NOTICE.md`](web/vendor/NOTICE.md)

</sub>
</div>

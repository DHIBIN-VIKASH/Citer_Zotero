# Z-Link / zotprep — plain-text citations → real, linked Zotero citations

Takes a manuscript with placeholder numbered citations (superscript `¹`, Lancet-style
`¹·²⁻⁴`, or `[1]`, `(1)`, `[2,3]`, `[1-3]`) plus a plain numbered bibliography,
resolves every reference to the **correct** paper, creates the Zotero items, and
writes a `.docx` ready for Zotero's ODF Scan.

It does not decide *where* a citation belongs — that's your judgment while
writing. It automates everything after: finding each paper, verifying it's
actually the right one, getting it into Zotero, and stamping the marker.

There are two front ends over one engine:

| | **Z-Link** (browser) | **zotprep** (command line) |
|---|---|---|
| Install | none | `pip install -r requirements.txt` |
| Runs | entirely in your browser | on your machine |
| Writes to Zotero | always | only with `--live` |
| Preview without writing | no | yes, `--dry-run` is the default |
| Review unresolved | click through the candidates | `--review` prompt |
| Remembers decisions | for the session | forever, in SQLite |

They share the matching engine, and the browser one is a verified port rather
than a reimplementation — see [Verifying the browser
port](#verifying-the-browser-port).

Measured on two real manuscripts:

| Manuscript | Refs | Resolved | In-text markers |
|---|---|---|---|
| Convergence in disease burden (epi + economics + books) | 35 | **35/35** | 47 |
| Osteoporosis burden in India (Lancet house style) | 47 | **47/47** | 23 groups |

Neither needed a manual review pass. The second surfaced four genuinely broken
bibliography entries that the gate refused to guess at — see
[Advisories](#advisories-when-a-reference-disagrees-with-itself).

---

## Z-Link — the browser version

Nothing to install. Open the page, drop in a `.docx`, and it resolves the
bibliography, adds the items to your Zotero library and gives you back the
scannable document and a report.

**Your manuscript never leaves the tab.** There is no server and no upload: the
page talks to Crossref, OpenAlex, Europe PMC, PubMed, Semantic Scholar and the
Zotero API directly from your browser, and every one of them permits that with
`Access-Control-Allow-Origin: *`. The page loads no third-party script at all —
even the `.docx` unzipping uses the browser's own Compression Streams — so there
is nothing on it that *could* send your work anywhere.

Your Zotero userID and API key are kept in that browser's local storage and sent
only to `api.zotero.org`, in a request header rather than a web address. Enter
them once. The **First time here** button walks through getting them, and
through installing the ODF Scan plugin, which is downloadable from the app
itself.

Differences from the CLI, all deliberate:

- **Every run writes to your library.** There is no dry-run mode. Credentials are
  checked before any searching starts, so a wrong key fails in about two seconds
  rather than after several minutes; existing items are matched on DOI and on
  title+year and reused rather than duplicated; and only references that cleared
  the accept gate become items at all.
- **Only unresolved references go to review**, where `--review` also asks about
  `from-reference-text` ones.
- **Nothing persists but your credentials.** Review decisions last for the
  session; the CLI writes them to SQLite forever.
- **The bibliography must be under a heading** in the document. There is no
  equivalent of `--bibliography`.

---

## Install (command line)

```bash
pip install -r requirements.txt
```

Python 3.11+. You also need the Zotero desktop app (7.0+) and the ODF Scan
plugin for the final conversion step — one-time, and covered under
[Zotero setup](#zotero-setup).

---

## Quick start

Save your credentials once. They go to `~/.zotprep/config.json`, created `0600`
under your user profile:

```bash
python -m zotprep --zotero-userid 1234567 --zotero-key YOUR_KEY --mailto you@email.com --save-credentials
```

Every run after that is just the manuscript. Dry run is the **default** —
nothing reaches your Zotero library unless you pass `--live` explicitly:

```bash
python -m zotprep --manuscript yourpaper.docx
```

Read `zot_out/report.csv`, then do the real run:

```bash
python -m zotprep --manuscript yourpaper.docx --live
```

Settings resolve highest-first: an explicit flag, then `$ZOTERO_USERID` /
`$ZOTERO_KEY` / `$ZOTPREP_MAILTO`, then the saved file — so a stored credential
never silently overrides one you asked for. `--show-config` prints where each
value is coming from, with the key masked; `--forget-credentials` deletes the
file.

Prefer not to save at all? The environment still works:

```bash
$env:ZOTERO_USERID = "1234567"; $env:ZOTERO_KEY = "your_key"
```

Avoid putting the key inline on a command line. It ends up in shell history, and
on this project it also ended up in a tool's approved-commands log — which is
exactly the sort of place a live key should not sit.

Re-running `--live` is safe: items are matched by DOI and by title+year against
your existing library and **reused, not duplicated**.

---

## Zotero setup

### The userID and API key

At https://www.zotero.org/settings/keys:

1. Your **userID** is the number in *"Your userID for use in API calls is …"*.
   It is not your username.
2. **Create new private key**, tick **Allow library access** *and* **Allow write
   access**. Without write access every run stops with an opaque `403`.
3. The key is shown **once**. Copy it immediately; if you lose it, delete that
   key and make another.

Revoke a key any time from the same page — it stops working immediately.

### The ODF Scan plugin

Needs Zotero 7.0 or newer. The `.xpi` is bundled at
[`web/vendor/`](web/vendor/) and downloadable from Z-Link's help panel, or from
the [upstream releases page](https://github.com/Juris-M/zotero-odf-scan-plugin/releases).

1. Save the `.xpi` to disk. If your browser offers to *install* it and then calls
   it corrupt, it has mistaken it for a browser extension — right-click and
   **Save link as…** instead.
2. In Zotero: **Tools → Plugins** (called Add-ons on older versions).
3. Gear icon ⚙ → **Install Plugin From File…**, pick the `.xpi`, restart if asked.

### Running the scan

1. **Tools → ODF Scan**
2. File type: **ODF (to citations)** — the direction that turns markers into live
   citations.
3. **Input File**: `zot_out/manuscript_scannable.docx`. **Output File**: somewhere
   else, so you keep the original.
4. **Next**, then **Finish**.

Open the result in your word processor → Zotero tab → Document Preferences →
choose a style → Add/Edit Bibliography.

---

## Input requirements

The manuscript `.docx` needs:

1. Body text with placeholder citations (see [Citation notation](#citation-notation) —
   most journal styles are handled).
2. A heading line reading `References`, `Bibliography`, `Works Cited`, or
   `Reference List`, followed by the numbered reference list.

If your references are in a separate file, pass `--bibliography refs.txt`.

A `Tables` / `Figures` / `Appendix` heading after the reference list ends the
bibliography, so captions are never imported as references.

Entry numbering may use `1.`, `1)`, `[1]`, or a bare number followed by
whitespace — including the **em space** (U+2003) that Lancet-family templates
emit with no delimiter at all. Reference formats handled:

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

That last form becomes a Zotero **bookSection** with the chapter title, the
containing `bookTitle`, edition, publisher, place and page range.

---

## Citation notation

In-text citations are located in **paragraph coordinates, not per run**, because
Word freely splits one citation across runs — `statement;²` + `⁰` is a single
citation that no per-run scan can see. Recognised:

| Notation | Example | Meaning |
|---|---|---|
| Bracketed | `[1]` `(2,3)` `[4-6]` | refs 1 / 2,3 / 4,5,6 |
| Word superscript formatting | run styled superscript | its digits |
| Unicode superscript | `²³` | ref 23 |
| Lancet separator (U+00B7 middle dot) | `¹·²⁻⁴` | refs 1, 2, 3, 4 |
| Superscript minus range (U+207B) | `⁴³⁻⁴⁵` | refs 43, 44, 45 |

### Superscripts that are *not* citations

Scientific manuscripts are full of superscripts that must never become
citations. Writing a Zotero marker into the middle of a number would corrupt your
results section, so these are rejected:

```
-13.3×10⁻³      exponent, not reference 3
1.60×10⁻⁴       exponent, not reference 4
R²=0.32         a square, not reference 2
R² increased    likewise
```

The rules: reject when the superscript is preceded by a digit, by `×`, or by
another superscript character (so `10⁻³` can't match on its `³` alone); when it
is followed by `=`; or when the preceding token is a lone letter variable.

Note that in Lancet house style the middle dot is *also* the decimal separator
(`39·1%`), which is why the superscript pattern must begin **and** end on a
superscript digit — a decimal point sits between ASCII digits and cannot match.

### Implausible ranges are flagged, not expanded

A superscript range spanning dozens of references is a dropped digit, not a
range. Both of these were real:

```
systems.³⁻³²      reads as refs 3-32   (30 refs) — meant ³⁰⁻³² or ³¹⁻³²
rates.¹¹⁻⁴²       reads as refs 11-42  (32 refs) — meant ⁴¹⁻⁴² or ¹¹·⁴²
```

Anything wider than 8 references is reported and the document text is **left
untouched**, rather than manufacturing citations the author never made. Ranges
whose endpoints aren't reference numbers at all (`(1990-2023)`, value spans) are
ignored silently.

---

## How matching works

The design principle: **find the correct paper first, then retrieve metadata** —
and accept a match only when several independent facts agree.

### Resolution order (cheapest and most certain first)

1. **Correction store** — you already decided this one in a past `--review`
   session. Returns immediately.
2. **Identifier in the reference text** — a DOI, PMID or PMCID is present, so
   fetch it directly. No searching at all.
3. **Resolution cache** — this exact reference was resolved in a previous run.
4. **Provider fan-out** — sources queried concurrently, using the extracted
   **title** (plus first author as a separate field), never the whole reference
   string.
5. **Accept gate** — hard conjunctions, described below.
6. **Build from reference text** — nothing indexes this paper, but the reference
   itself is complete.

### The accept gate

Acceptance is a **conjunction of independent signals**, not a weighted score
crossing a threshold. A wrong paper can fake one signal; it cannot
simultaneously match the title, the year, the volume *and* the first page.

| Tier | Requirement | Confidence |
|---|---|---|
| `identifier` | DOI/PMID/PMCID in the reference, resolved directly | 1.00 |
| `ecitmatch` | NCBI citation matcher hit **and** title agreement ≥ 0.92 | 0.99 |
| `fingerprint` | title ≥0.90 **and** year **and** exact volume **and** exact first page | 0.97 |
| `title+journal+author` | title ≥0.93 **and** year **and** journal **and** author | 0.93 |
| `provider-agreement` | 2+ providers returned the same DOI, title ≥0.88 | 0.90 |
| `exact-title` | normalized titles identical **and** year **and** author | 0.88 |
| `from-reference-text` | no index holds it; item built from the reference itself | 0.80 |

Anything failing every tier becomes `{NEEDS REVIEW: ref n}` in the document and
is **not** created in Zotero. You never get a live citation silently pointing at
the wrong paper.

### Why `ecitmatch` matters — and where it lies

NCBI's citation matcher takes `journal|year|volume|first_page|author` and returns
an exact PMID. It's a **lookup, not a search** — no relevance ranking, nothing to
score. Vancouver references already carry all five fields, so most biomedical
references resolve deterministically. It also works with the **author field
blank**, which is what rescues consortium references (`GBD 2021 Collaborators`)
whose "first author" is not a personal surname.

But it does **not** reliably return `NOT_FOUND` when the citation is off. For
journals that number articles rather than pages (`J Clin Med` 4923, `Injury`
111528) and for supplements (`16(Suppl 2):S233`) it can return a confidently
wrong PMID for an unrelated paper. That is why the tier carries a title guard.

The guard is **0.92**, chosen from measurement rather than taste: across the two
manuscripts, 42 of 43 genuine `ecitmatch` hits scored ≥0.98, while a reference
whose volume/pages had been copied from a sibling paper in the same journal issue
scored 0.83.

### Journal abbreviation matching, without a lookup table

NLM-style abbreviations have a useful property: every abbreviated token is a
prefix of the corresponding full word, in order. Tokens are paired positionally
and prefix-checked:

```
J Polit Econ            ≡ Journal of Political Economy
J R Stat Soc Series B   ≡ Journal of the Royal Statistical Society: Series B
Bull World Health Organ ≡ Bulletin of the World Health Organization
```

Token counts must match exactly, which is what makes `Lancet` ≠ `Lancet
Oncology`. Verified 21/21 correct and 7/7 near-misses rejected.

### A known limit of title similarity

`token_set_ratio` scores **1.00** whenever one title's tokens are a subset of the
other's, so a short reference title matches a longer unrelated paper perfectly:

```
ref : "Renal osteodystrophy and chronic kidney disease-mineral bone disorder"
cand: "Mitochondrial dysfunction and mitophagy blockade contribute to renal
       osteodystrophy in chronic kidney disease-mineral bone disorder"
```

Damping the score by token-count ratio was tried and **reverted**: legitimate
matches are also subsets, because journals expand titles in the version of
record. The GBD 2021 reference is 24 tokens in a bibliography and 45 in Crossref
once `(YLDs)`, `(DALYs)`, `811 subnational locations` and the trailing
`a systematic analysis for the Global Burden of Disease Study 2021` are included.
Any penalty strong enough to reject the unrelated subset also rejects that.

So the inflation is left alone and handled where it belongs: the gate never takes
title similarity on its own. The unrelated-subset case above is rejected because
its journal *and* authors both disagree — a conjunction no string metric has to
resolve.

---

## Advisories: when a reference disagrees with itself

Some references are internally inconsistent. These are reported after the run and
in the `advisory` column of `report.csv`. They are **advisories, not blockers** —
the title is usually what the author meant, and the correct paper is generally
found anyway.

**Locator points at a different paper.** The volume/pages were copied from a
neighbouring reference:

> `[13]` reference's journal/volume/pages (Lancet Glob Health 2018;6:e1363) point
> to a DIFFERENT paper than its title — the volume/pages were probably copied
> from a neighbouring reference.

**Title matches a real paper by other authors in another journal.** A genuine
miscitation usually gets the authors right and fumbles the volume. A title
attached to the wrong authors *and* the wrong journal is characteristic of a
fabricated entry — and the named authors are typically real researchers in the
field, which is exactly what makes it invisible on a read-through:

> `[32]` VERIFY THIS ENTRY: the title matches a real paper
> (10.1016/j.archger.2024.105519, Archives of Gerontology and Geriatrics 2024)
> whose authors (Harvey, Payne, Tan) and journal differ entirely from this
> reference (Sheehan, Injury).

To avoid crying wolf, this one additionally requires comparable title lengths
(short titles like *Economic Growth* are subsets of countless papers) and no
loose surname agreement (`Mendez` vs `Mendez-Guerra` is not a discrepancy).

Advisories are suppressed for references a real tier corroborated — a disagreeing
citation-matcher hit there is just `ecitmatch` noise.

---

## Search providers

| Provider | Key needed | Cost | Role |
|---|---|---|---|
| **Crossref** | no | free, unmetered | Primary. All DOI'd literature including economics and books. Also the metadata source of record once a DOI is known. |
| **Europe PMC** | no | free, unmetered | Biomedical workhorse. Returns `medlineAbbreviation` — the exact style Vancouver uses — plus volume/issue/pages. |
| **PubMed** | no | free | `ecitmatch` deterministic lookup, plus `[Title]`-field search. |
| **OpenAlex** | no | **metered** | Supplementary. Broad coverage including books. |
| **Semantic Scholar** | yes | free with key | **Off by default.** Set `SEMANTIC_SCHOLAR_API_KEY` to enable. |

Provider caveats worth knowing, all learned the hard way:

- **OpenAlex is now metered.** Unauthenticated callers get a small daily budget
  (~100 requests, resets midnight UTC) then return `429 "Insufficient budget"`.
  One 35-reference run can exhaust it. zotprep detects that specific response and
  **disables the provider for the rest of the run** instead of retrying. Accuracy
  does not depend on it.
- **Semantic Scholar 429s on nearly every unauthenticated call**, and its
  required ~1.1s pacing serializes the whole run for a provider returning
  nothing. Wired up but off until you supply a free key.
- **Crossref rejects the entire request over one bad `select` field.** A single
  unsupported field name (`edition-number`) returns HTTP 400 for *every*
  reference — silently zeroing out the provider while the run still "succeeds"
  with fewer votes. Keep `SELECT` in `search/crossref.py` in sync with the API
  and don't add fields speculatively. 4xx responses now fail fast and print
  loudly rather than being retried four times.
- **Europe PMC returns zero hits, not an error, for a malformed query.** Its
  query language treats `: " ( ) [ ] { } ~ ^ ? *` and bare `AND`/`OR`/`NOT` as
  syntax, so titles are sanitized before use.

A `search/google.py` provider can be dropped into `zotprep/search/` and added to
the fan-out list without changing anything else. It was never needed to reach
100%.

---

## The last mile: `--review`

```bash
python -m zotprep --manuscript yourpaper.docx --review
```

For anything that didn't pass the gate, prints the top candidates with title,
authors, journal, year, volume/pages, DOI and which providers found each:

```
choose 1-5 / [s]kip / [d]oi / [b]uild from reference text:
```

Every decision is written to the `corrections` table, so **the same reference
never asks twice** — in this manuscript or any future one. A reference you fix
once is permanently resolved.

Z-Link shows the same candidates as cards after a run, with the same four
outcomes and the same effect on each reference:

| | result |
|---|---|
| pick a candidate | `ACCEPTED`, tier `manual`, confidence 1.0 |
| paste a DOI | `ACCEPTED`, tier `manual`, confidence 1.0 |
| build from the reference text | `FROM_TEXT`, tier `book-from-text`, confidence 0.80 |
| leave flagged | unchanged — stays `{NEEDS REVIEW: n}` |

Building from the reference text is offered only for book-shaped references,
which is where the CLI offers `[b]`. A pasted DOI still goes through the Crossref
enrichment step, so the item is built from the canonical record rather than the
bare identifier — you supply the DOI, not the metadata. Web decisions last for
the session only; the CLI's are permanent.

---

## Outputs

Written to `zot_out/` (or `--outdir`):

| File | Contents |
|---|---|
| `manuscript_scannable.docx` | Your document with Scannable Cite markers `{ \| Author, (Year) \| \| \|zu:USERID:ITEMKEY}` in place of the placeholder numbers, old bibliography removed |
| `report.csv` | `n, status, tier, confidence, doi, pmid, zotero_key, resolved_title, reason, advisory, raw_reference` |

**Read `report.csv` before scanning.** `tier` tells you *why* each match was
accepted, which is more informative than the confidence number. Rows with tier
`from-reference-text` had no external corroboration — the metadata came from your
own bibliography, so it's only as good as what you typed.

---

## Caching

`database/cache.sqlite`, two tables:

- `resolutions` — memoized API results, keyed on the normalized reference text.
  Safe to delete; only costs time to rebuild. Failures are never cached, since
  provider indexes improve over time.
- `corrections` — your `--review` decisions. **Do not delete this** unless you
  want to re-answer everything.

Because the key is the *normalized* reference text, renumbering your bibliography
or changing whitespace/dashes/case still hits the cache. Disable with
`--no-cache`.

---

## All options

| Flag | Required? | Meaning |
|---|---|---|
| `--manuscript FILE` | yes | `.docx` input |
| `--bibliography FILE` | no | Separate reference-list file, if not under a "References" heading |
| `--outdir DIR` | no | Output folder (default `zot_out`) |
| `--mailto EMAIL` | no | Crossref/NCBI polite pool. Also `$ZOTPREP_MAILTO`, or saved |
| `--dry-run` | no | Resolve and report, create nothing. **This is the default** |
| `--live` | to write | Create/reuse items in your Zotero library. Must be explicit — never implied |
| `--zotero-userid ID` | with `--live` | Numeric userID. Also `$ZOTERO_USERID`, or saved |
| `--zotero-key KEY` | with `--live` | Read/write API key. Also `$ZOTERO_KEY`, or saved |
| `--review` | no | Prompt interactively for anything not auto-accepted |
| `--workers N` | no | Concurrent references (default 12). Per-provider rate limits enforced separately |
| `--no-cache` | no | Ignore and don't write the SQLite cache |
| `--save-credentials` | no | Write the userID/key/mailto in use to `~/.zotprep/config.json` and stop asking |
| `--forget-credentials` | no | Delete that file |
| `--show-config` | no | Print where each setting comes from, key masked, then exit |

`$ZOTPREP_CONFIG` overrides the config file location, which is what the tests
use to avoid touching a real one.

---

## Verifying the browser port

Rewriting a verified engine in another language risks silent behaviour drift, so
that risk is measured rather than argued away:

```bash
python web/tools/parity/run_all.py
```

Two phases, and both matter.

**Parity** compares each ported module against its Python original over a corpus
and requires the identical IEEE-754 double — not a value within a tolerance. The
accept gates are hard thresholds (0.88 / 0.90 / 0.92 / 0.93), so a last-bit
difference is a behaviour difference.

**Mutation** then injects known port mistakes one at a time and requires each to
be caught. Parity passing alone proves nothing: three times during the port it
reported a clean pass for code that was provably wrong, because the corpus never
reached the relevant branch. Current score is **47/47 killable mutants killed**,
with 6 documented as equivalent — each carrying the argument for why no test can
kill it.

Differences between the two languages that produced real bugs, all caught here:

- `rapidfuzz` computes `(1 - d/l) * 100` in `ratio` but `100 - 100*d/l` in
  `token_set_ratio` — algebraically equal, different doubles
- Python's `len()` and `sorted()` work on code points, JavaScript's on UTF-16
  code units
- Python's `\w`, `\b` and `isupper()` are Unicode-aware; JavaScript's are ASCII
- `str.replace` replaces every occurrence, `String.replace` replaces one
- `format(x, ".2f")` rounds half to even, `toFixed` rounds half away from zero

The `.docx` layer needs a DOM, so it has its own check against a fixture built by
the Python original:

```bash
python web/tools/parity/docx_fixture.py
python -m http.server 8731 --directory web
# then open http://localhost:8731/tools/parity/docx_check.html
```

End to end, the same document through `python -m zotprep --dry-run --no-cache`
and through the browser produces the same status, tier, confidence, DOI and title
for every reference, the same rewritten-citation count, the same unresolved list,
and the same advisory strings character for character.

See [`web/README.md`](web/README.md) for the layout and for how to add cases.

---

## Module layout

```
zotprep/                  the engine and the CLI
  cli.py            arg parsing, end-to-end flow
  config.py         saved credentials, and the flag/env/file precedence
  extractor.py      reference string -> ParsedRef (identifiers, title, journal, locator, book/chapter fields)
  models.py         ParsedRef, Candidate, Resolution
  resolver.py       orchestration, from-text fallback, advisories
  scorer.py         signals + the accept gate
  utils.py          normalization, title similarity, journal abbreviation matching
  cache.py          SQLite resolutions + corrections
  review.py         interactive confirm loop
  docx_writer.py    bibliography parsing, citation-span detection, docx mutation
  search/
    base.py         shared client, per-provider rate limiting, retries, quota detection
    crossref.py  europepmc.py  pubmed.py  openalex.py  semanticscholar.py
  zotero/
    client.py       item building, duplicate detection, creation

web/                      Z-Link: the browser build
  index.html        the app, including the first-run help
  app.js            UI wiring; credentials <-> localStorage
  src/              the port — one file per zotprep module it mirrors,
                    plus zip.js (Compression Streams) and pipeline.js
  vendor/           the ODF Scan plugin .xpi, with its NOTICE
  tools/parity/     the differential tests and the mutation check
.github/workflows/pages.yml   runs parity, then deploys web/ to Pages

database/cache.sqlite
tests/
  refs_35.json      35-reference fixture
  run_35.py         accuracy harness
```

---

## Deploying Z-Link

`.github/workflows/pages.yml` runs the parity suite on every push and publishes
to GitHub Pages only if it passes, so a drifting engine cannot ship. It stages
`index.html`, `app.js`, `src/` and `vendor/` — the parity harnesses stay out,
since they are development tooling that depends on fixtures which are
deliberately not committed.

Enable it once under **Settings → Pages → Source: GitHub Actions**. The
repository must be public for Pages on a free account.

---

## Known limitations

- Only body paragraphs are scanned. Citations inside **tables, footnotes, text
  boxes or endnotes are not** rewritten.
- `from-reference-text` items are only as accurate as your typed bibliography.
  Nothing external verified them. `complete_enough_for_text_item()` refuses to
  build one from a reference it couldn't fully parse — those go to review.
- Books rarely have a locator to fingerprint against, so they lean on
  `exact-title` or the from-text path.
- Metadata enrichment re-fetches the canonical Crossref record once a DOI is
  known, which improves creator names but drops the PMID from most items. DOI is
  the identifier that matters for citation styles.
- The parser is tuned for Vancouver and Lancet house style. Other formats may
  need one `--review` pass, after which they're cached forever.
- Z-Link needs a browser with Compression Streams — Chrome/Edge 80+, Firefox
  113+, Safari 16.4+. It says so up front rather than failing later.
- Z-Link has no dry run and no correction store, so a manuscript reviewed and
  then reloaded asks again. Use the CLI when you want a preview pass or
  decisions that persist.

---

## Predecessor

`zotprep.py` (v3, single file) is still in the repo. It used Crossref's
`query.bibliographic` on the whole reference string plus a single fuzzy ratio
against a reconstructed citation. That ratio measures shared *boilerplate*
(journal abbreviation, volume, page range) rather than paper identity, so it sat
in the 0.35–0.55 band for correct and incorrect matches alike — and a 0.55
threshold admitted wrong papers. It scored roughly 20/35. Kept for reference
only; use `python -m zotprep`.

# How Z-Link works

Everything the [README](../README.md) folds away: what it accepts, how a match is
decided, why it sometimes refuses, and every flag.

- [Input requirements](#input-requirements)
- [Citation notation](#citation-notation)
- [How matching works](#how-matching-works)
- [Advisories](#advisories)
- [Search providers](#search-providers)
- [Outputs](#outputs)
- [Caching](#caching)
- [All options](#all-options)
- [Known limitations](#known-limitations)

For how the browser build is proven equivalent to the Python engine, see
[`web/README.md`](../web/README.md).

---

## Input requirements

The manuscript `.docx` needs:

1. Body text with placeholder citations — see [Citation notation](#citation-notation)
2. A heading reading `References`, `Bibliography`, `Works Cited` or `Reference List`,
   followed by the numbered list

The bibliography ends at the first thing that plainly is not a reference: a
`Tables` / `Figures` / `Appendix` heading, a figure or table caption (`Figure 1.`,
`Table 2`), a table, or a paragraph carrying an image. Captions are never imported as
references, and everything from that point on — legends, images, tables, appendices,
and the section breaks that give a landscape table page its orientation — is left
exactly as you wrote it. Only the heading and the reference entries are removed. CLI
only: `--bibliography refs.txt` if the references live in a separate file.

In-text citations are rewritten *before* the bibliography only, so numbers inside
trailing figure legends and table captions stay as they are.

A document whose bibliography parses but whose citations are recognised **nowhere** stops
the run before anything is resolved or added, and the reference list is never removed when
nothing has replaced it — a manuscript that comes back with neither citations nor a
bibliography is worse than one that was refused.

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
Manchikanti L, Singh V. Epidemiology of low back pain. Neuromodulation. 2014;17 Suppl 2:3-10.
Bhatia R, Chopra G. Efficacy of platelet rich plasma. J Clin Diagn Res. 2016;10(9):UC05-7.
```

The last two are the supplement volume (`17 Suppl 2` → volume 17) and the two-letter page
prefix (`UC05`). Both matter more than they look: the locator carries journal, volume and
first page, so a locator the pattern cannot read leaves the accept gate with nothing to
agree on and the reference is rejected outright rather than matched loosely.

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
| Plain digits after a space | `knee OA 2.` `analgesia 6,7.` | 2 / 6,7 |

<details>
<summary><b>Plain digits that are <i>not</i> citations</b></summary>

<br>

The space-separated form is what a superscript citation becomes when a manuscript is
pasted as plain text, and it is the one notation genuinely ambiguous with prose —
`25 patients`, `Group 4` and `for 12 months` all have the same shape. It is accepted only
when the sentence before it ended, the clause after it ends, or the digits come as a
comma-separated list, and rejected when:

```
Group 4, Table 2, grade 2-3      the number labels the word before it
of 25, than 12, into 4 groups    a function word a citation never follows
By month 1, at months 1, 3, 6    a timepoint label rather than a unit
4-6 times, 4,326 patients        a measurement the digits belong to
(baseline 62.14                  a decimal, not a sentence that ended
2, 3, 1 and 4 losses             a list that runs backwards
```

`3 months 6,8.` is a citation, though: the unit already took its own number, so what
follows it is not part of the measurement.

</details>

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

## Outputs

Written to `zot_out/` (or `--outdir`); downloaded directly in the browser.

| File | Contents |
|:--|:--|
| `manuscript_zotero.docx` | Your document with live Zotero citations — Word field codes (`ADDIN ZOTERO_ITEM CSL_CITATION`) replacing the placeholder numbers, and the old bibliography — heading and entries, nothing else — removed. Open it in Word; no plugin, no conversion step |
| `manuscript_scannable.docx` | The same document with Scannable Cite markers `{ \| Author, (Year) \| \| \|zu:USERID:ITEMKEY}` instead, for the ODF Scan plugin. CLI: `--style scannable`. The browser produces both |
| `report.csv` | `n, status, tier, confidence, doi, pmid, zotero_key, resolved_title, reason, advisory, raw_reference` |

One citation, however many references it carries: `6,7` becomes a single field of two
items, which is how Zotero models it. A group where only some references resolved is split
— the resolved part becomes a field, the rest stays a visible `{NEEDS REVIEW: n}`, so a
half-resolved citation cannot look finished.

> **On ODF Scan.** It converts by scanning `document.xml` as text, looking for
> brace-delimited markers. A Word picture carries `uri="{GUID}"` attributes of the same
> shape, and a manuscript with images came back with a citation written into the middle of
> one — a file Word refuses to open. Writing the fields directly is what removes that
> failure mode, along with the conversion step itself.

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
[`web/vendor/NOTICE.md`](../web/vendor/NOTICE.md)

</sub>
</div>


# Z-Link — browser build

A static, browser-only build of the zotprep engine, for GitHub Pages. No server,
no backend, no proxy: the page talks to Crossref, OpenAlex, Europe PMC, PubMed,
Semantic Scholar and the Zotero API directly. All six send
`Access-Control-Allow-Origin: *`, which is what makes that possible.

The Zotero API key stays in the browser's `localStorage` and is sent only to
`api.zotero.org`, in a request header rather than a query string.

The Python package it is ported from keeps its own name, `zotprep` — the file
headers throughout `src/` name the module each one mirrors, and those references
are meant to stay accurate.

## Every run writes to the library

There is no dry-run mode. Three things do the work a preview pass used to:

- **Credentials are verified before any resolution starts.** A wrong key fails
  in about two seconds rather than after several minutes of searching.
- **Existing items are matched and reused**, on DOI and on normalised
  title+year, so re-running a document does not create second copies.
- **Only references that passed the accept gate become items.** Anything
  unresolved stays a `{NEEDS REVIEW: n}` marker in the document rather than
  becoming a guess in someone's library.

The review step is where an ambiguous reference gets decided, and nothing
reaches the library without either clearing the gate or being chosen there.

## Why a port and not Pyodide

Rewriting the engine in JavaScript risks silent behaviour drift from the
verified Python one. That risk is not argued away here — it is measured. Every
ported module is compared against its Python original over a corpus, and the
comparison is exact: floats are compared as IEEE-754 bit patterns, not within a
tolerance, because the accept gates in `scorer.js` are hard thresholds
(0.88 / 0.90 / 0.92 / 0.93) and a last-bit difference can flip an ACCEPTED
reference into REVIEW.

```bash
python web/tools/parity/run_all.py
```

### Status

| module | Python original | how it is verified |
|---|---|---|
| `fuzz.js` | `rapidfuzz.fuzz` | exact-float parity, 744k comparisons |
| `utils.js` | `zotprep/utils.py` | exact-float parity, every primitive |
| `models.js` | `zotprep/models.py` | — (data classes; defaults are load-bearing) |
| `extractor.js` | `zotprep/extractor.py` | exact parity on every ParsedRef field |
| `scorer.js` | `zotprep/scorer.py` | exact parity: signals, confidence, accept, rank order |
| `zip.js` | (python-docx internals) | round-trip: saved file re-opens and re-reads identically |
| `docx.js` | `zotprep/docx_writer.py` | `tools/parity/docx_check.html`, against a Python-built fixture |
| `search/*.js` | `zotprep/search/*` | end-to-end run vs the CLI on the same document |
| `resolver.js` | `zotprep/resolver.py` | end-to-end run vs the CLI on the same document |
| `zotero.js` | `zotprep/zotero/client.py` | end-to-end (dry run) vs the CLI |
| `review.js` | `zotprep/review.py` | decision semantics checked against the four CLI outcomes |
| `pipeline.js` | `run()` in `zotprep/cli.py` | end-to-end vs the CLI |

The end-to-end check is the one that ties it together: the same .docx through
`python -m zotprep --dry-run --no-cache` and through the browser produces the
same status, tier, confidence, DOI and title for every reference, the same
number of rewritten citations, the same unresolved list, and the same advisory
and warning strings character for character.

Note that the CLI keeps `--dry-run`, and that is what makes this comparison
possible without touching a real library. Only the browser build requires the
write.

### The layers that are not float-parity tested

`docx.js` and the provider clients cannot be compared with `run_all.py` — one
needs a DOM, the others need the network. They have their own checks:

```bash
python web/tools/parity/docx_fixture.py     # build the fixture
python -m http.server 8731 --directory web  # then open:
#   http://localhost:8731/tools/parity/docx_check.html
```

The fixture is a real .docx containing every shape the marker logic has to
survive: citations split across runs, Word superscript *formatting* as opposed
to superscript characters, the Lancet middle-dot separator, digits fused onto
sentence punctuation, mathematics that must not become citations (`10⁻³`,
`R²=0.32`), an oversized range that must be refused, and a year span that is not
a citation at all.

## The two phases, and why both are needed

`run_all.py` runs parity first, then mutation testing. **Parity passing on its
own means nothing.** Twice during this port a harness reported a clean pass for
a port that was provably wrong, because its corpus never reached the relevant
branch:

- `str.strip(" .,;")` was only ever tested on strings with nothing strippable at
  the *leading* end, so a port that stripped only the trailing end passed.
- the `split(":")` journal variant was masked by the parenthesis-stripping
  variant already succeeding on every case in the bank.

Mutation testing injects known port mistakes one at a time and requires each to
be caught. Current score: **47/47 killable mutants killed**, with 6 mutants
documented as *equivalent* — the edit provably cannot change any result, so no
test can kill it. Each equivalence claim carries its argument in
`mutation_check.py`; if one ever starts failing, that argument has broken.

## Differences that actually bite, Python vs JavaScript

These are the ones that produced real bugs here, all caught by the harnesses:

| | Python | JavaScript | handled by |
|---|---|---|---|
| `\w`, `\b` | Unicode-aware on `str` | ASCII-only | explicit `\p{L}\p{N}_` classes with `u` |
| `len()`, indexing | code points | UTF-16 code units | `codePoints()` in `fuzz.js` |
| `sorted()` on str | code point order | UTF-16 code unit order | `byCodePoint` comparator |
| `str.replace(a, b)` | every occurrence | first occurrence only | `split().join()` |
| `str.strip(chars)` | any of `chars`, both ends | `trim()` is whitespace only | `stripChars()` |
| `format(x, ".2f")` | half to even | `toFixed` is half away from zero | `fixed2()` in `scorer.js` |
| `sort(reverse=True)` | stable descending | — | explicit comparator, ties return 0 |
| `str.isupper()` | Unicode-aware | — | `\p{Lu}` / `\p{Ll}` |

`rapidfuzz` itself contributed one: `fuzz.ratio` computes `(1 - d/l) * 100`
while `token_set_ratio` computes `100 - 100*d/l`. Those are algebraically equal
and produce different doubles — `66.66666666666667` against
`66.66666666666666`. Both spellings are reproduced where rapidfuzz uses them.

## Layout

```
web/
  index.html           the app, including the first-run help dialog
  app.js               UI wiring; credentials <-> localStorage
  src/
    fuzz.js            rapidfuzz's three metrics
    utils.js           normalisation + comparison primitives
    models.js          ParsedRef / Candidate / Resolution
    extractor.js       reference string -> ParsedRef
    scorer.js          signals, confidence, accept gates, ranking
    search/            base + crossref, openalex, europepmc, pubmed, s2
    resolver.js        orchestration, cheapest-first
    zip.js             ZIP via Compression Streams, no library
    docx.js            the python-docx slice + marker rewriting
    zotero.js          item building and the api.zotero.org client
    pipeline.js        the whole job, mirroring cli.run()
  tools/parity/
    run_all.py         run everything (start here)
    harness.py         shared comparison plumbing
    *_parity.py        one differential test per module
    *_runner.mjs       the JS side of each
    mutation_check.py  proves the harnesses can fail
    docx_fixture.py    builds the .docx fixture + Python's expected output
    docx_check.html    the docx comparison (needs a DOM)
```

Adding to a `*_parity.py` corpus is cheap and always worth it; adding a mutant
to `mutation_check.py` is how you find out whether the addition was needed.

## The review step

Anything that resolves to nothing gets a card after the run: the reference as
written, why the gate rejected it, and up to five candidates with the fields
needed to tell them apart — authors, journal, year, volume/page, identifier,
item type, and which providers returned it. That is the same information
`review.py` prints, and the same candidates in the same order.

The outcomes match the CLI exactly:

| | result |
|---|---|
| pick a candidate | `ACCEPTED`, tier `manual`, confidence 1.0 |
| paste a DOI | `ACCEPTED`, tier `manual`, confidence 1.0 |
| build from the reference text | `FROM_TEXT`, tier `book-from-text`, confidence 0.80 |
| leave flagged | unchanged — stays `{NEEDS REVIEW: n}` in the document |

"Build from the reference text" is offered only for book-shaped references,
which is where `review.py` offers `[b]`: a complete Vancouver book reference
already carries every field Zotero needs, so no index has to hold it.

A pasted DOI still goes through `enrich()`, so the item is built from the
canonical Crossref record rather than from the bare identifier — the reader
supplies the DOI, not the metadata.

Two deliberate differences from `--review`:

- Only `REVIEW` entries are offered. The CLI also asks about `FROM_TEXT` ones.
- Decisions last for the session. The CLI writes each to its corrections table
  so the same reference never asks twice; this build persists nothing but
  credentials.

`Apply & rebuild` re-runs the output half of the pipeline. That half re-reads
the document from the original bytes each time rather than reusing the parsed
one, because `markBody()` and `removeRange()` mutate in place — without that, a
second pass would mark up already-marked text and delete from an
already-truncated body. It is safe to review and rebuild repeatedly.

## Running it locally

```bash
python -m http.server 8731 --directory web
```

ES modules need a real origin, so opening `index.html` from the filesystem will
not work.

## Deploying

`.github/workflows/pages.yml` runs the parity suite and, only if it passes,
publishes `index.html`, `app.js` and `src/` to GitHub Pages. The harnesses are
not published — they are development tooling and depend on fixtures that are
deliberately not committed.

Enable it once under **Settings → Pages → Source: GitHub Actions**. The
repository has to be public for Pages on a free account.

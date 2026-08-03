/**
 * Port of zotprep/docx_writer.py, plus the slice of python-docx it relies on.
 *
 * Two things live here:
 *
 *   1. A minimal Document/Paragraph/Run model over word/document.xml, matching
 *      python-docx's semantics closely enough that the marker logic below is a
 *      transcription rather than a reinterpretation. The details that matter are
 *      called out on each class — particularly that `paragraphs` and `runs` are
 *      *direct children only*, which is what keeps table contents and
 *      hyperlink-wrapped runs out of the citation scan in both implementations.
 *
 *   2. The bibliography extraction and citation-marker rewriting themselves.
 *      Marker logic is carried over from the original script — it worked. Only
 *      references that passed the accept gate become live Scannable Cite
 *      markers; anything else stays visibly unresolved in the document rather
 *      than silently pointing at the wrong paper.
 */
import { readZip, writeZip } from './zip.js';

const W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';
const XML_NS = 'http://www.w3.org/XML/1998/namespace';

// --- Python character-class helpers ------------------------------------------
// Python's str.isdigit() is true for superscript digits (¹²³ are Numeric_Type
// Digit but category No), which JavaScript's \d and even \p{Nd} both miss. That
// difference decides whether "10⁻³" is read as an exponent or as a citation, so
// it is reproduced rather than approximated.
const SUP_DIGITS = '⁰¹²³⁴⁵⁶⁷⁸⁹';
const SUP_SEPS = '⁻·';

function isPyDigit(ch) {
  if (!ch) return false;
  return /\p{Nd}/u.test(ch) || SUP_DIGITS.includes(ch) || /[²³¹]/.test(ch);
}
function isPyAlpha(ch) {
  return Boolean(ch) && /\p{L}/u.test(ch);
}
function isPyDigitString(s) {
  return s.length > 0 && [...s].every(isPyDigit);
}

// --- the python-docx slice ----------------------------------------------------

/**
 * One `w:r`. python-docx's Run.text joins the run's `w:t` content and renders
 * `w:tab` as a tab and `w:br`/`w:cr` as newlines; assigning to it clears those
 * children and leaves a single `w:t`. Both are reproduced, because the citation
 * scanner works in paragraph-text coordinates and any drift between the text it
 * measures and the text it edits would land the marker in the wrong place.
 */
export class Run {
  constructor(el) { this.el = el; }

  get text() {
    let out = '';
    for (const child of this.el.childNodes) {
      if (child.nodeType !== 1 || child.namespaceURI !== W) continue;
      const tag = child.localName;
      if (tag === 't') out += child.textContent;
      else if (tag === 'tab') out += '\t';
      else if (tag === 'br' || tag === 'cr') out += '\n';
    }
    return out;
  }

  set text(value) {
    for (const child of [...this.el.childNodes]) {
      if (child.nodeType === 1 && child.namespaceURI === W
          && ['t', 'tab', 'br', 'cr'].includes(child.localName)) {
        this.el.removeChild(child);
      }
    }
    const t = this.el.ownerDocument.createElementNS(W, 'w:t');
    // Word drops leading/trailing spaces without this, which would silently
    // reflow the document around every edited citation.
    t.setAttributeNS(XML_NS, 'xml:space', 'preserve');
    t.textContent = value;
    this.el.appendChild(t);
  }

  get rPr() {
    for (const child of this.el.childNodes) {
      if (child.nodeType === 1 && child.namespaceURI === W && child.localName === 'rPr') {
        return child;
      }
    }
    return null;
  }

  get superscript() {
    const rPr = this.rPr;
    if (!rPr) return null;
    for (const child of rPr.childNodes) {
      if (child.nodeType === 1 && child.namespaceURI === W && child.localName === 'vertAlign') {
        const val = child.getAttributeNS(W, 'val') || child.getAttribute('w:val');
        if (val === 'superscript') return true;
        if (val === 'baseline') return false;
        return null;
      }
    }
    return null;
  }

  set superscript(value) {
    let rPr = this.rPr;
    if (!rPr) {
      rPr = this.el.ownerDocument.createElementNS(W, 'w:rPr');
      this.el.insertBefore(rPr, this.el.firstChild);
    }
    let va = null;
    for (const child of rPr.childNodes) {
      if (child.nodeType === 1 && child.namespaceURI === W && child.localName === 'vertAlign') {
        va = child;
        break;
      }
    }
    if (value === null) {
      if (va) rPr.removeChild(va);
      return;
    }
    if (!va) {
      va = this.el.ownerDocument.createElementNS(W, 'w:vertAlign');
      rPr.appendChild(va);
    }
    va.setAttributeNS(W, 'w:val', value ? 'superscript' : 'baseline');
  }
}

/**
 * One `w:p`. `runs` returns only direct `w:r` children, exactly as python-docx
 * does — runs nested inside `w:hyperlink` are excluded from both the text and
 * the edit, in both implementations.
 */
export class Paragraph {
  constructor(el) { this.el = el; }

  get runs() {
    const out = [];
    for (const child of this.el.childNodes) {
      if (child.nodeType === 1 && child.namespaceURI === W && child.localName === 'r') {
        out.push(new Run(child));
      }
    }
    return out;
  }

  get text() {
    return this.runs.map((r) => r.text).join('');
  }
}

/**
 * The document. `paragraphs` are the body-level `w:p` elements only, matching
 * python-docx: paragraphs inside tables are not included, so a table of results
 * is never scanned for citations by either implementation.
 */
export class Document {
  constructor(entries, xmlDoc) {
    this.entries = entries;
    this.xml = xmlDoc;
  }

  static async load(arrayBuffer) {
    const entries = await readZip(arrayBuffer);
    const main = entries.find((e) => e.name === 'word/document.xml');
    if (!main) throw new Error('Not a Word document (word/document.xml is missing).');
    const text = new TextDecoder('utf-8').decode(main.data);
    const xml = new DOMParser().parseFromString(text, 'application/xml');
    const err = xml.querySelector('parsererror');
    if (err) throw new Error(`Could not parse word/document.xml: ${err.textContent.slice(0, 200)}`);
    return new Document(entries, xml);
  }

  get body() {
    return this.xml.getElementsByTagNameNS(W, 'body')[0];
  }

  get paragraphs() {
    const body = this.body;
    if (!body) return [];
    const out = [];
    for (const child of body.childNodes) {
      if (child.nodeType === 1 && child.namespaceURI === W && child.localName === 'p') {
        out.push(new Paragraph(child));
      }
    }
    return out;
  }

  async save() {
    const xmlText = new XMLSerializer().serializeToString(this.xml);
    const data = new TextEncoder().encode(xmlText);
    const entries = this.entries.map((e) => (
      e.name === 'word/document.xml' ? { ...e, data } : e
    ));
    return writeZip(entries);
  }
}

// --- bibliography extraction and marker rewriting -----------------------------

const HEADERS = /^\s*(references|bibliography|works cited|reference list)\s*:?\s*$/i;

// Superscript citation notation. Beyond the digits, journal templates in the
// Lancet family use U+00B7 MIDDLE DOT as the separator between citations and
// U+207B SUPERSCRIPT MINUS for ranges, so "¹·²⁻⁴" means refs 1, 2-4.
//
// The same middle dot is that house style's decimal separator ("39·1%"), which
// is why SUPRUN must begin and end with a superscript digit: a decimal point
// sits between ASCII digits and therefore cannot match.
const SUP_MAP = new Map();
[...SUP_DIGITS].forEach((ch, i) => SUP_MAP.set(ch, String(i)));
SUP_MAP.set('⁻', '-');
SUP_MAP.set('·', ',');
const translateSup = (s) => [...s].map((c) => SUP_MAP.get(c) ?? c).join('');

const SUPRUN = new RegExp(`[${SUP_DIGITS}](?:[${SUP_DIGITS}${SUP_SEPS}]*[${SUP_DIGITS}])?`, 'gu');
const GROUP = /[[(]\s*([\d\s,\-–—]+?)\s*[\])]/g;

// Digits fused directly onto sentence punctuation with no space:
//   "...better-resourced systems.29-32"   "...differing rates.41-42"
// This is what a superscript citation degrades into when the formatting is lost,
// and it is otherwise unambiguous: ordinary prose puts a space after a full stop.
//
// The lookbehind demands a *letter* before the punctuation, which keeps decimals
// out — in "p=0.05" the character before the stop is a digit. Any survivor that
// isn't a real reference number is dropped later by expand().
const PLAIN_ATTACHED = /(?<=[A-Za-z][.;,!?])(\d{1,3}(?:\s*[-–—]\s*\d{1,3})?(?:\s*,\s*\d{1,3}(?:\s*[-–—]\s*\d{1,3})?)*)/g;

// Entry numbering: "1. Smith J", "[1] Smith J", "1) Smith J", and — as produced
// by Lancet-family templates — "1<em-space>Smith J" with no delimiter at all.
// The delimiter is therefore optional, but separating whitespace is not: without
// that requirement a title beginning with a digit would be truncated.
const LEAD = /^\s*[[(]?(\d{1,3})[\])\.]?[\s  -   　  ]+(.*)$/;

// Unicode spaces that must be folded to ASCII before any of the above matches.
const WEIRD_SPACE = /[  -   　  ]/g;
const STOP_HEADINGS = /^\s*(tables?|figures?|appendix|supplement\w*)\s*:?\s*$/i;

// A figure or table caption, which ends the bibliography as surely as a heading
// does. Trailing matter often has no heading at all — the legends simply begin —
// so the caption is the only marker that the reference list has stopped. Tried
// only after LEAD has failed, so a numbered reference whose title happens to
// start "Table ..." is still read as a reference.
const CAPTION = new RegExp(
  '^\\s*(?:supplementary|supplemental|appendix|online)?\\s*'
  + '(?:figures?|figs?|tables?|charts?|box(?:es)?|schemes?|panels?|exhibits?)'
  + '\\s*\\.?\\s*(?:\\d|[SE]\\d|[IVX]+[.\\s:]|[A-Z][.\\s:])',
  'i',
);

// Widest plausible citation range. Beyond this, treat it as a typo, not a range.
const MAX_RANGE_SPAN = 8;

export function findBiblioIndex(doc) {
  const ps = doc.paragraphs;
  for (let i = 0; i < ps.length; i++) {
    if (HEADERS.test(ps[i].text)) return i;
  }
  return null;
}

/**
 * Split a bibliography block into a Map of number -> reference text.
 *
 * Exotic Unicode spaces are folded to ASCII first. Journal templates in the
 * Lancet family separate the entry number from the author with an em space
 * (U+2003) and no delimiter; left unfolded, every entry fails to match, the
 * numbering fallback kicks in, and the digits stay glued to the first author's
 * surname — which silently corrupts author matching for the whole document.
 */
export function parseBibliography(text) {
  const folded = text.replace(WEIRD_SPACE, ' ');
  const entries = new Map();
  let cur = [];
  let num = null;
  for (const line of folded.split('\n')) {
    if (STOP_HEADINGS.test(line)) break;
    const m = LEAD.exec(line);
    if (m) {
      if (num !== null) entries.set(num, cur.join(' ').trim());
      num = parseInt(m[1], 10);
      cur = [m[2]];
    } else if (line.trim() && num !== null) {
      cur.push(line.trim());
    }
  }
  if (num !== null) entries.set(num, cur.join(' ').trim());

  if (!entries.size) {
    // Unnumbered list: take paragraph order, but still stop at a trailing
    // Tables/Figures section rather than importing its captions as references.
    let i = 0;
    for (const line of folded.split('\n')) {
      if (STOP_HEADINGS.test(line)) break;
      if (line.trim()) entries.set(++i, line.trim());
    }
  }
  return entries;
}

function titleCase(s) {
  return String(s).replace(/[A-Za-z]+/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());
}

/**
 * Return render(spec) -> replacement text for a citation group like '1,3-5'.
 *
 * Oversized ranges are refused rather than expanded. A superscript range
 * spanning dozens of references is a dropped digit in the manuscript ("³⁻³²"
 * where "³¹⁻³²" was meant), and expanding it would manufacture 30 citations the
 * author never made. Those are collected in `warnings` for the caller to report,
 * and the document text is left untouched.
 */
export function makeRenderer(resolutions, keys, uid, {
  style = 'scannable', refs = null, warnings = null,
} = {}) {
  function expand(spec) {
    const out = [];
    for (const rawChunk of spec.replace(/–/g, '-').replace(/—/g, '-').split(',')) {
      const chunk = rawChunk.trim();
      if (chunk.includes('-')) {
        const i = chunk.indexOf('-');
        const a = chunk.slice(0, i);
        const b = chunk.slice(i + 1);
        if (isPyDigitString(a.trim()) && isPyDigitString(b.trim())) {
          const lo = parseInt(a, 10);
          const hi = parseInt(b, 10);
          if (!resolutions.has(lo) && !resolutions.has(hi)) {
            // Neither endpoint is a reference number, so this is ordinary prose
            // — a year span like "(1990-2023)" or a value range. Not a citation,
            // and not worth a warning.
            return [];
          }
          if (hi - lo > MAX_RANGE_SPAN) {
            if (warnings) {
              warnings.push(
                `range '${lo}-${hi}' spans ${hi - lo + 1} references — `
                + 'likely a dropped digit in the manuscript; left unchanged',
              );
            }
            return [];
          }
          for (let n = lo; n <= hi; n++) out.push(n);
        }
      } else if (isPyDigitString(chunk)) {
        out.push(parseInt(chunk, 10));
      }
    }
    return out.filter((n) => resolutions.has(n));
  }

  /**
   * Human-readable half of the marker. Zotero renders the real citation from the
   * stored item, so this only has to be legible — but take the surname from the
   * manuscript, which preserves casing the matcher folded away ("McMichael", not
   * "Mcmichael").
   */
  function label(n) {
    const c = resolutions.get(n).candidate;
    if (!c) return `ref ${n}`;
    const ref = refs ? refs.get(n) : null;
    const who = (ref ? ref.corporate : null)
      || c.corporate
      || (ref && ref.authors && ref.authors.length ? ref.authors[0] : null)
      || (c.authors && c.authors.length ? titleCase(c.authors[0]) : 'Anon');
    return `${who}, (${c.year || 'n.d.'})`;
  }

  return function render(spec) {
    const nums = expand(spec);
    if (!nums.length) return null;
    const parts = [];
    for (const n of nums) {
      const res = resolutions.get(n);
      if (['ACCEPTED', 'FROM_TEXT'].includes(res.status) && keys.get(n)) {
        parts.push(style === 'scannable'
          ? `{ | ${label(n)} | | |zu:${uid}:${keys.get(n)}}`
          : `{${label(n)}}`);
      } else {
        parts.push(`{NEEDS REVIEW: ref ${n}}`);
      }
    }
    return parts.join('');
  };
}

/**
 * True when a superscript run is mathematics, not a citation.
 *
 * Scientific manuscripts are full of superscripts that must never become
 * citations. Writing a Zotero marker into the middle of a number would corrupt
 * the results section: in "-13.3x10⁻³" the exponent is not reference 3, and in
 * "R²=0.32" the square is not reference 2.
 */
function isMathSuperscript(text, start, end) {
  const before = start > 0 ? text[start - 1] : '';
  const after = end < text.length ? text[end] : '';

  // 1. exponent attached to a number: "10⁻³", "2⁸"
  if (isPyDigit(before)) return true;
  // 1b. the run begins part-way into a superscript expression. SUPRUN must start
  //     on a digit, so "10⁻³" yields a match on the "³" alone, preceded by the
  //     superscript minus. A citation never starts mid-superscript.
  if (SUP_SEPS.includes(before) || before === '×') return true;
  // 2. an assignment follows: "R²=0.32"
  if (after === '=') return true;
  // 3. a lone-letter variable precedes: "R²", "n²" — but not "et al²⁰", where
  //    the preceding token is a real word.
  if (isPyAlpha(before)) {
    let tokenStart = start - 1;
    while (tokenStart > 0 && isPyAlpha(text[tokenStart - 1])) tokenStart--;
    if (start - tokenStart === 1) return true;
  }
  return false;
}

/** [[runIndex, start, end]] in paragraph-text coordinates. */
function runSpans(p) {
  const spans = [];
  let pos = 0;
  p.runs.forEach((r, i) => {
    const len = r.text.length;
    spans.push([i, pos, pos + len]);
    pos += len;
  });
  return spans;
}

/**
 * Locate every citation marker in a paragraph as [start, end, numberSpec].
 *
 * Works in paragraph-text coordinates rather than per-run, because Word freely
 * splits a single citation across runs — "statement;²" + "⁰" is one citation,
 * and no per-run scan can see it.
 */
function citationSpans(p) {
  const text = p.text;
  const found = [];

  for (const m of text.matchAll(GROUP)) found.push([m.index, m.index + m[0].length, m[1]]);
  for (const m of text.matchAll(SUPRUN)) {
    const s = m.index;
    const e = m.index + m[0].length;
    if (isMathSuperscript(text, s, e)) continue;
    found.push([s, e, translateSup(m[0])]);
  }
  for (const m of text.matchAll(PLAIN_ATTACHED)) {
    const s = m.index + m[0].indexOf(m[1]);
    found.push([s, s + m[1].length, m[1]]);
  }

  // Contiguous Word-superscript formatting, merged across run boundaries.
  const runs = p.runs;
  let start = null;
  for (const [i, s] of runSpans(p)) {
    const isSup = runs[i].superscript === true && Boolean(runs[i].text.trim());
    if (isSup && start === null) start = s;
    else if (!isSup && start !== null) {
      found.push([start, s, text.slice(start, s)]);
      start = null;
    }
  }
  if (start !== null) found.push([start, text.length, text.slice(start)]);

  // Drop overlaps, preferring the earliest and longest match.
  found.sort((a, b) => (a[0] - b[0]) || ((b[1] - b[0]) - (a[1] - a[0])));
  const out = [];
  let lastEnd = -1;
  for (const [s, e, spec] of found) {
    if (s < lastEnd || !/\d/.test(spec)) continue;
    out.push([s, e, spec]);
    lastEnd = e;
  }
  return out;
}

/**
 * Replace paragraph-coordinate spans, distributing edits across runs.
 *
 * Applied right-to-left so that earlier spans' offsets stay valid, which is why
 * the run map is computed once from the original text and never refreshed.
 */
function applySpans(p, replacements) {
  const runs = p.runs;
  const map = runSpans(p);
  for (const [s, e, rep] of [...replacements].sort((a, b) => b[0] - a[0])) {
    let first = true;
    const edits = new Map();
    for (const [i, rs, re] of map) {
      if (re <= s || rs >= e) continue;
      const txt = runs[i].text;
      const localS = Math.max(s, rs) - rs;
      const localE = Math.min(e, re) - rs;
      if (first) {
        edits.set(i, txt.slice(0, localS) + rep + txt.slice(localE));
        first = false;
      } else {
        edits.set(i, txt.slice(0, localS) + txt.slice(localE));
      }
    }
    for (const [i, t] of edits) {
      runs[i].text = t;
      // the marker is body text; superscripting it would break ODF Scan
      runs[i].superscript = false;
    }
  }
}

/** Rewrite in-text citations before the bibliography. Returns markers written. */
export function markBody(doc, biblioIdx, render) {
  let count = 0;
  for (const p of doc.paragraphs.slice(0, biblioIdx)) {
    if (!p.text.trim()) continue;
    const replacements = [];
    for (const [s, e, spec] of citationSpans(p)) {
      const rep = render(spec);
      if (rep) replacements.push([s, e, rep]);
    }
    if (replacements.length) {
      applySpans(p, replacements);
      count += replacements.length;
    }
  }
  return count;
}

function hasImage(el) {
  return ['drawing', 'pict', 'object']
    .some((tag) => el.getElementsByTagNameNS(W, tag).length > 0);
}

/**
 * Paragraph index one past the end of the bibliography block.
 *
 * A manuscript does not end at its reference list. Figure legends and the images
 * they caption, table captions, the tables themselves and appendices all come
 * after it, and deleting to the end of the document takes every one of them. Two
 * of those losses are worse than they look:
 *
 *   - Word merges two tables that end up as adjacent siblings, so deleting the
 *     captions between three tables silently fuses them into one.
 *   - A `w:sectPr` in a paragraph's `w:pPr` is the section break that *ends* the
 *     section that paragraph belongs to. Delete the paragraph and the break goes
 *     with it, and everything before it joins the following section — one
 *     landscape table page at the end of a manuscript is enough to turn the whole
 *     document landscape.
 *
 * So the block is bounded here, and only the block is removed. It ends at the
 * first thing that plainly is not a reference: a table, a Tables/Figures/Appendix
 * heading, a figure or table caption, or a paragraph carrying an image. Trailing
 * blank paragraphs are left where they are — they are the author's page-break
 * spacing, not part of the bibliography.
 */
export function biblioEndIndex(doc, biblioIdx) {
  const ps = doc.paragraphs;
  let end = biblioIdx + 1; // the heading itself always goes
  for (let i = biblioIdx + 1; i < ps.length; i++) {
    const { el } = ps[i];
    const prev = el.previousElementSibling;
    if (prev && prev.namespaceURI === W && prev.localName === 'tbl') break;
    const text = ps[i].text.replace(WEIRD_SPACE, ' ');
    if (STOP_HEADINGS.test(text)) break;
    if (!LEAD.test(text) && CAPTION.test(text)) break;
    if (hasImage(el)) break;
    if (text.trim()) end = i + 1;
  }
  return end;
}

/**
 * Remove paragraphs [start, end), keeping any section break they carry.
 *
 * A paragraph holding a `w:sectPr` is emptied rather than removed, because the
 * break describes the page setup of everything *before* it (see biblioEndIndex).
 * What is left is one empty paragraph carrying the break — the same section
 * boundary, in the same place, with nothing of the bibliography still in it.
 */
export function removeRange(doc, start, end) {
  for (const p of doc.paragraphs.slice(start, end)) {
    const { el } = p;
    let pPr = null;
    for (const child of el.childNodes) {
      if (child.nodeType === 1 && child.namespaceURI === W && child.localName === 'pPr') {
        pPr = child;
        break;
      }
    }
    let sect = null;
    if (pPr) {
      for (const child of pPr.childNodes) {
        if (child.nodeType === 1 && child.namespaceURI === W && child.localName === 'sectPr') {
          sect = child;
          break;
        }
      }
    }
    if (!sect) {
      el.parentNode.removeChild(el);
      continue;
    }
    for (const child of [...el.childNodes]) {
      if (child !== pPr) el.removeChild(child);
    }
    for (const child of [...pPr.childNodes]) {
      if (child !== sect) pPr.removeChild(child);
    }
  }
}

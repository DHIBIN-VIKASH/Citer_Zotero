/**
 * Port of zotprep/extractor.py — raw reference string in, ParsedRef out.
 *
 * The Python original opens by noting that the whole accuracy strategy rests on
 * this file: everything downstream compares candidates against these extracted
 * fields, so a bad parse here becomes a bad match later. That makes it the
 * second-most parity-sensitive file after the string metric, and the one where
 * Python and JavaScript regex semantics differ most.
 *
 * The differences that actually bite, and how each is handled:
 *
 *   \b and \w      Python's are Unicode-aware on `str`; JavaScript's are
 *                  ASCII-only. `\bPMID\b` in Python does NOT match inside
 *                  "PMIDé" (é is a word character) but the JS equivalent does.
 *                  Every word boundary here is therefore written explicitly as
 *                  a `(?<![\p{L}\p{N}_])` / `(?![\p{L}\p{N}_])` pair, and `\w`
 *                  as `[\p{L}\p{N}_]`, with the `u` flag.
 *
 *   str.replace    Python's replaces *every* occurrence; JavaScript's replaces
 *                  only the first when given a string. The identifier-stripping
 *                  step relies on the Python behaviour, so it uses split/join.
 *
 *   $              Python's `$` also matches before a trailing newline;
 *                  JavaScript's does not. Inputs here are whitespace-collapsed
 *                  by nfkc() first, so no trailing newline ever reaches these
 *                  patterns.
 *
 *   isupper/islower  Unicode-aware in Python. Written as \p{Lu} / \p{Ll}.
 *
 *   str.strip(chars)  removes any of several characters; String.trim() does not.
 *
 * Verified field-by-field against the Python original by
 * tools/parity/extractor_parity.py.
 */
import { ParsedRef } from './models.js';
import { rstripChars, stripChars } from './utils.js';

// Unicode-aware word-boundary assertions, matching Python's \b on str.
const NWB = '(?<![\\p{L}\\p{N}_])';
const NWA = '(?![\\p{L}\\p{N}_])';
const W = '[\\p{L}\\p{N}_]';

// --- identifier patterns -----------------------------------------------------
// DOI: trailing punctuation is stripped afterwards, since refs end in "."
const DOI_RE = new RegExp(`${NWB}10\\.\\d{4,9}/[^\\s"<>,;]+`, 'iu');
const DOI_TRAILING = /[.,;:)\]}>'"]+$/;
const PMID_RE = new RegExp(`${NWB}PMID:?\\s*(\\d{4,9})${NWA}`, 'iu');
const PMID_RE_G = new RegExp(PMID_RE.source, 'giu');
const PMCID_RE = new RegExp(`${NWB}(PMC\\d{4,9})${NWA}`, 'iu');
const PMCID_RE_G = new RegExp(PMCID_RE.source, 'giu');

// --- structural patterns -----------------------------------------------------
const LEAD_NUM = /^\s*[[(]?\s*(\d{1,3})\s*[.)\]]\s+/;
const BRACKET_NOTE = /\[[^\]]*\]/g;
const YEAR_RE = new RegExp(`${NWB}(19|20)\\d{2}${NWA}`, 'gu');

// "2017;390(10111):2437-60"  /  "2012;10:1"  /  "2004;S2:11-44"
const LOCATOR_SRC =
  `${NWB}(?<year>(?:19|20)\\d{2})\\s*;\\s*` +
  '(?<vol>[A-Za-z]?[\\dA-Za-z]{0,8}?)\\s*' +
  '(?:\\(\\s*(?<issue>[^)]{1,20})\\s*\\))?\\s*' +
  ':\\s*(?<fp>[A-Za-z]?\\d+)(?:\\s*-\\s*(?<lp>[A-Za-z]?\\d+))?';
const LOCATOR_RE = new RegExp(LOCATOR_SRC, 'u');

// Book imprint tail. Both separators before the year occur in the wild:
//   "Amsterdam: North-Holland; 1967"      (Vancouver)
//   "Amsterdam: North-Holland, 1967"      (Lancet house style)
//   "Amsterdam: Elsevier, 2013: 1113-36"  (chapter, with page range)
const BOOK_TAIL_RE = new RegExp(
  '^(?<place>[^:;]{2,40}):\\s*(?<publisher>[^;,]{2,60})[;,]\\s*' +
  '(?<year>(?:19|20)\\d{2})' +
  '(?:\\s*:\\s*(?<fp>[A-Za-z]?\\d+)(?:\\s*-\\s*(?<lp>[A-Za-z]?\\d+))?)?\\.?$',
  'u',
);
const EDITION_RE = /^(?<ed>\d+(?:st|nd|rd|th)|rev(?:ised)?|\d+)\s*edn?\.?$/iu;
// trailing ", 4th edn" attached to a book title rather than standing alone
const INLINE_EDITION_RE = /,\s*(?<ed>\d+(?:st|nd|rd|th)|rev(?:ised)?)\s*edn?\.?\s*$/iu;
const INLINE_EDITION_RE_G = new RegExp(INLINE_EDITION_RE.source, 'giu');
const CHAPTER_IN_RE = /^In:\s*(?<editors>.+?),?\s*eds?\.?$/iu;

const CORPORATE_HINT = new RegExp(
  `${NWB}(?:collaborators?|collaboration|group|consortium|committee|initiative|` +
  'investigators|network|study team|working party|organization|organisation|' +
  `who|unicef|world health)${NWA}`,
  'iu',
);

// A segment is an author list if it looks like "Surname AB, Surname CD" —
// i.e. mostly "word + 1-4 capital initials" tokens.
const AUTHOR_TOKEN = new RegExp(
  `^(?<sur>(?:[A-Z]${W}*(?:\\s+[a-z]{2,3})?(?:[-\\s][A-Z]${W}*)*))` +
  '\\s+(?<init>(?:[A-Z]\\.?){1,4})$',
  'u',
);
// Python's `[\w'’-]` — \w plus apostrophes and hyphen. Rebuilt here because the
// class above interpolates W; kept identical in content and order.
const AUTHOR_TOKEN_FULL = new RegExp(
  `^(?<sur>(?:[A-Z][\\p{L}\\p{N}_'’-]*(?:\\s+[a-z]{2,3})?(?:[-\\s][A-Z][\\p{L}\\p{N}_'’-]*)*))` +
  '\\s+(?<init>(?:[A-Z]\\.?){1,4})$',
  'u',
);
const SENTENCE_SPLIT = /(?<=[.?!])\s+/;
const INITIALS_ONLY = /^(?:[A-Z]\.?){1,4}$/u;
const ET_AL = /,?\s*et al\.?$/iu;

/** Python's `str.split()` with no argument. */
function words(s) {
  const t = String(s ?? '').trim();
  return t ? t.split(/\s+/) : [];
}

/** Normalize unicode and unify the dash/quote zoo found in pasted refs. */
export function nfkc(s) {
  let t = String(s ?? '').normalize('NFKC');
  for (const bad of '‐‑‒–—―−') t = t.split(bad).join('-');
  for (const [bad, good] of [['‘', "'"], ['’', "'"], ['“', '"'], ['”', '"']]) {
    t = t.split(bad).join(good);
  }
  return t.replace(/\s+/g, ' ').trim();
}

/** Vancouver abbreviates end pages: 2437-60 means 2437-2460, e1339-51 -> e1351. */
export function expandPage(first, last) {
  if (!first || !last) return last ?? null;
  const preF = /^([A-Za-z]*)(\d+)$/.exec(first);
  const preL = /^([A-Za-z]*)(\d+)$/.exec(last);
  if (!(preF && preL)) return last;
  const fdig = preF[2];
  let ldig = preL[2];
  if (ldig.length < fdig.length) ldig = fdig.slice(0, fdig.length - ldig.length) + ldig;
  return (preL[1] || preF[1]) + ldig;
}

/**
 * Split a reference into sentence-ish segments.
 *
 * Vancouver puts a period after the last author's initials and after "et al.",
 * so a plain sentence split lands exactly on the author/title/journal
 * boundaries. Mid-list authors are separated by commas, not periods, so they
 * survive the split intact.
 */
function splitSegments(text) {
  return text.split(SENTENCE_SPLIT).map((p) => p.trim()).filter(Boolean);
}

function looksLikeAuthors(seg) {
  let s = rstripChars(seg, '.');
  s = s.replace(ET_AL, '');
  const chunks = s.split(',').map((c) => c.trim()).filter(Boolean);
  if (!chunks.length) return false;
  const hits = chunks.filter((c) => AUTHOR_TOKEN_FULL.test(c)).length;
  return hits >= Math.max(1, Math.floor(chunks.length / 2));
}

/** Return [personal surnames, corporate name or null]. */
function parseAuthors(seg) {
  let s = rstripChars(seg.trim(), '.');
  s = s.replace(ET_AL, '');
  if (CORPORATE_HINT.test(s) && !looksLikeAuthors(s + '.')) return [[], s];
  const surnames = [];
  for (const rawChunk of s.split(',')) {
    const chunk = rawChunk.trim();
    if (!chunk) continue;
    const m = AUTHOR_TOKEN_FULL.exec(chunk);
    if (m) {
      surnames.push(m.groups.sur.trim());
    } else {
      // "Surname, Given" (APA) or an un-initialled name
      const parts = words(chunk);
      const word = parts.length ? parts[0] : '';
      if (/^\p{Lu}/u.test(word.slice(0, 1)) && word.length > 1 && !INITIALS_ONLY.test(word)) {
        surnames.push(word);
      }
    }
  }
  if (!surnames.length && CORPORATE_HINT.test(s)) return [[], s];
  return [surnames, null];
}

export function parseReference(n, raw) {
  const ref = new ParsedRef(n, String(raw).trim());
  let text = nfkc(raw);
  text = text.replace(LEAD_NUM, '');

  // --- identifiers: pull, then remove so they don't pollute segmentation ---
  let m = DOI_RE.exec(text);
  if (m) {
    ref.doi = m[0].replace(DOI_TRAILING, '').toLowerCase();
    // Python's str.replace removes every occurrence, not just the first
    text = text.split(m[0]).join(' ');
  }
  m = PMID_RE.exec(text);
  if (m) {
    ref.pmid = m[1];
    text = text.replace(PMID_RE_G, ' ');
  }
  m = PMCID_RE.exec(text);
  if (m) {
    ref.pmcid = m[1].toUpperCase();
    text = text.replace(PMCID_RE_G, ' ');
  }

  // editorial annotations like "[Zotero: complete vol/pages/DOI]" are noise
  text = text.replace(BRACKET_NOTE, ' ');
  text = text.replace(new RegExp(`${NWB}(?:doi|available from|accessed)${NWA}[:.]?\\s*`, 'giu'), ' ');
  text = text.replace(/https?:\/\/\S+/g, ' ');
  text = text.replace(/\s+/g, ' ').trim();

  // --- locator (journal;volume(issue):pages) -------------------------------
  const loc = LOCATOR_RE.exec(text);
  if (loc) {
    const g = loc.groups;
    ref.year = parseInt(g.year, 10);
    ref.volume = (g.vol || '').trim() || null;
    ref.issue = (g.issue || '').trim() || null;
    ref.first_page = g.fp ?? null;
    ref.last_page = expandPage(g.fp, g.lp);
  }

  const segments = splitSegments(text);
  if (!segments.length) return ref;

  // --- author segment ------------------------------------------------------
  const [authors, corporate] = parseAuthors(segments[0]);
  ref.authors = authors;
  ref.corporate = corporate;
  const body = segments.slice(1);

  // --- book chapter: "In: <editors>, eds. <book title>[, Nth edn]. <imprint>"
  for (let i = 0; i < body.length; i++) {
    if (CHAPTER_IN_RE.test(rstripChars(body[i], '.') + '.')) {
      ref.is_book = true;
      ref.is_chapter = true;
      ref.title = rstripChars(body.slice(0, i).join(' '), '.').trim();
      const rest = body.slice(i + 1);
      if (rest.length) {
        let bt = rstripChars(rest[0], '.').trim();
        const em = INLINE_EDITION_RE.exec(bt);
        if (em) {
          ref.edition = em.groups.ed;
          bt = bt.replace(INLINE_EDITION_RE_G, '').trim();
        }
        ref.book_title = bt;
      }
      for (const seg2 of rest.slice(1)) {
        const bm = BOOK_TAIL_RE.exec(seg2.trim());
        if (bm) {
          ref.place = bm.groups.place.trim();
          ref.publisher = bm.groups.publisher.trim();
          ref.year = parseInt(bm.groups.year, 10);
          ref.first_page = bm.groups.fp ?? null;
          ref.last_page = expandPage(bm.groups.fp, bm.groups.lp);
          break;
        }
      }
      ref.title = stripChars(ref.title.replace(/\s+/g, ' '), ' .,;');
      return ref;
    }
  }

  // --- edition marker ------------------------------------------------------
  for (let i = 0; i < body.length; i++) {
    const em = EDITION_RE.exec(rstripChars(body[i], '.'));
    if (em) {
      ref.edition = em.groups.ed;
      body.splice(i, 1);
      break;
    }
  }

  // --- locate the tail: either a locator segment or a book imprint ---------
  let tailIdx = null;
  for (let i = 0; i < body.length; i++) {
    if (LOCATOR_RE.test(body[i])) { tailIdx = i; break; }
    const bm = BOOK_TAIL_RE.exec(body[i]);
    if (bm) {
      ref.is_book = true;
      ref.place = bm.groups.place.trim();
      ref.publisher = bm.groups.publisher.trim();
      ref.year = ref.year ?? parseInt(bm.groups.year, 10);
      if (bm.groups.fp) {
        ref.first_page = ref.first_page ?? bm.groups.fp;
        ref.last_page = ref.last_page ?? expandPage(bm.groups.fp, bm.groups.lp);
      }
      tailIdx = i;
      break;
    }
  }

  if (tailIdx === null) {
    // No locator and no imprint: fall back to the last segment that is just a
    // year (e.g. "Lancet. 2024.") — then journal is the segment before it.
    for (let i = body.length - 1; i >= 0; i--) {
      if (/^(?:19|20)\d{2}\.?$/u.test(body[i].trim())) {
        tailIdx = i;
        ref.year = ref.year ?? parseInt(rstripChars(body[i].trim(), '.'), 10);
        break;
      }
    }
  }

  if (tailIdx === null) {
    // last resort: title is everything, year from anywhere in the string
    ref.title = rstripChars(body.join(' '), '.').trim();
  } else if (ref.is_book) {
    ref.title = rstripChars(body.slice(0, tailIdx).join(' '), '.').trim();
  } else {
    // segment immediately before the tail is the journal, unless the tail
    // segment itself carries the journal ("Lancet 2017;390(1):1-2")
    const jrnIdx = tailIdx - 1;
    const tailSeg = body[tailIdx];
    const locHit = LOCATOR_RE.exec(tailSeg);
    const preLocator = locHit ? stripChars(tailSeg.slice(0, locHit.index), ' ,.;') : '';
    if (preLocator && !/^(?:19|20)\d{2}$/u.test(preLocator)) {
      ref.journal = preLocator;
      ref.title = rstripChars(body.slice(0, tailIdx).join(' '), '.').trim();
    } else if (jrnIdx >= 0) {
      ref.journal = rstripChars(body[jrnIdx], '.').trim();
      ref.title = rstripChars(body.slice(0, jrnIdx).join(' '), '.').trim();
    } else {
      ref.title = rstripChars(body.slice(0, tailIdx).join(' '), '.').trim();
    }
  }

  if (ref.year === null) {
    const years = text.match(YEAR_RE);
    if (years && years.length) ref.year = parseInt(years[years.length - 1], 10);
  }

  // a book with no imprint but an edition marker is still a book
  if (!ref.is_book && ref.publisher === null && ref.volume === null && ref.edition) {
    ref.is_book = true;
  }

  ref.title = stripChars(ref.title.replace(/\s+/g, ' '), ' .,;');
  ref.journal = stripChars(ref.journal.replace(/\s+/g, ' '), ' .,;');
  return ref;
}

export function parseAll(refs) {
  const out = new Map();
  for (const [n, txt] of refs instanceof Map ? refs : Object.entries(refs)) {
    out.set(Number(n), parseReference(Number(n), txt));
  }
  return out;
}

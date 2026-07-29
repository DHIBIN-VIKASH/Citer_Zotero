/**
 * Port of zotprep/scorer.py — candidate scoring and the accept/reject gate.
 *
 * The design note from the Python original applies unchanged, and is the reason
 * this port must be exact rather than approximate:
 *
 *   `confidence()` produces a number, used only to *rank* candidates.
 *   `accept()` produces a decision, using hard gates on independent signals.
 *
 *   A wrong paper can fake one signal. It cannot simultaneously fake the title,
 *   the year, and the volume/first-page pair — that combination is effectively a
 *   unique key. So acceptance requires a conjunction, never a weighted sum
 *   crossing a line.
 *
 * Because the gates are hard thresholds on a float, the arithmetic in
 * confidence() is written in the same order and the same groupings as the
 * Python source: floating-point addition is not associative, so reordering the
 * terms would produce a different double and could move a candidate across a
 * boundary.
 *
 * Two JavaScript-specific hazards are handled explicitly:
 *
 *   Tuple ordering  Python sorts by a tuple and compares booleans as 0/1, with
 *                   `reverse=True` meaning a *stable* descending sort — not an
 *                   ascending sort that is then reversed. compareKeys() below
 *                   reproduces that, and Array.prototype.sort is stable per
 *                   spec, so ties keep provider order.
 *
 *   Float printing  Python's format(x, ".2f") rounds half to even; JavaScript's
 *                   Number.toFixed rounds half away from zero. They disagree on
 *                   exactly representable ties such as 0.125 ("0.12" vs
 *                   "0.13"), which are reachable here — a similarity of 0.125
 *                   comes out of any 8-character comparison at distance 7. The
 *                   reason strings end up in the report the author reads, so
 *                   fixed2() implements Python's rule.
 *
 * Verified against the Python original by tools/parity/scorer_parity.py.
 */
import {
  journalMatch, normText, pageEqual, titleSimilarity, volumeEqual,
} from './utils.js';

// Aggregators that mirror publisher records rather than host them. When a
// duplicate pair is otherwise tied, prefer the publisher's own DOI.
export const SECONDARY_DOI_PREFIXES = ['10.2307/'];

// Minimum title agreement before a citation-matcher hit is trusted. Measured
// across two manuscripts, 42 of 43 genuine ecitmatch hits scored >= 0.98, while
// a reference whose volume/pages had been copied from a sibling paper in the
// same journal issue scored 0.83 — so this cleanly separates "title formatted
// differently" from "this is a different paper".
export const ECITMATCH_TITLE_GUARD = 0.92;

/**
 * Python's format(x, ".2f"): round the exact binary value to two decimals,
 * ties to even. toFixed(100) is exact for the magnitudes involved here, so the
 * rounding decision can be made on the true decimal expansion rather than on a
 * value that has already been rounded once.
 */
export function fixed2(x) {
  if (!Number.isFinite(x)) return String(x);
  const neg = x < 0 || Object.is(x, -0);
  const s = Math.abs(x).toFixed(100);
  const dot = s.indexOf('.');
  const digits = s.slice(0, dot) + s.slice(dot + 1);
  const intLen = dot;
  const keep = intLen + 2; // digits retained before rounding
  let head = digits.slice(0, keep);
  const rest = digits.slice(keep);

  const first = rest.charCodeAt(0) - 48;
  const restNonZero = /[1-9]/.test(rest.slice(1));
  let roundUp;
  if (first > 5) roundUp = true;
  else if (first < 5) roundUp = false;
  else if (restNonZero) roundUp = true;
  else roundUp = ((head.charCodeAt(head.length - 1) - 48) % 2) === 1; // tie -> even

  if (roundUp) {
    head = (BigInt(head) + 1n).toString().padStart(head.length, '0');
  }
  if (head.length > keep) { // carried into a new integer digit, e.g. 9.99 -> 10.00
    return (neg ? '-' : '') + head.slice(0, head.length - 2) + '.' + head.slice(-2);
  }
  const ip = head.slice(0, head.length - 2) || '0';
  return (neg ? '-' : '') + ip + '.' + head.slice(-2);
}

export class Signals {
  constructor() {
    this.title_sim = 0.0;
    this.year_delta = null;
    this.author_ok = false;
    this.author_overlap = 0.0;
    this.corporate_ok = false;
    this.journal_ok = false;
    this.volume_ok = false;
    this.first_page_ok = false;
    this.last_page_ok = false;
    this.type_ok = true;
    this.n_providers = 1;
    this.ecitmatch = false;
  }

  get locator_ok() {
    return this.volume_ok && this.first_page_ok;
  }

  get year_ok() {
    return this.year_delta !== null && this.year_delta <= 1;
  }

  /** Human-readable reasons, for the review report. */
  failing() {
    const out = [];
    if (this.ecitmatch && this.title_sim < ECITMATCH_TITLE_GUARD) {
      out.push(
        'THE REFERENCE CONTRADICTS ITSELF: its journal/year/volume/pages '
        + 'identify a different paper than its title. Check the bibliography '
        + "entry — the volume or page numbers were likely copied from a "
        + 'neighbouring reference',
      );
    }
    if (this.title_sim < 0.90) out.push(`title similarity ${fixed2(this.title_sim)}`);
    if (this.year_delta === null) out.push('candidate has no year');
    else if (this.year_delta > 1) out.push(`year differs by ${this.year_delta}`);
    if (!this.locator_ok) {
      const missing = [];
      if (!this.volume_ok) missing.push('volume');
      if (!this.first_page_ok) missing.push('first page');
      out.push('no ' + missing.join('/') + ' match');
    }
    if (!this.journal_ok) out.push('journal mismatch');
    if (!(this.author_ok || this.corporate_ok)) out.push('author mismatch');
    return out;
  }
}

/**
 * Distinctive part of a consortium name, for substring probing.
 *
 * "GBD 2021 Low Back Pain Collaborators" -> "gbd 2021 low back pain".
 * The generic tail words are dropped because providers render them
 * inconsistently or omit them entirely.
 */
function consortiumKey(corporate) {
  let txt = normText(corporate);
  for (const tail of ['collaborators', 'collaborator', 'collaboration', 'group',
    'consortium', 'committee', 'investigators', 'network']) {
    txt = txt.split(tail).join(' '); // Python str.replace: every occurrence
  }
  return txt.split(/\s+/).filter(Boolean).join(' ');
}

export function signals(ref, cand) {
  const s = new Signals();
  s.title_sim = titleSimilarity(ref.title, cand.title);
  if (ref.year && cand.year) s.year_delta = Math.abs(ref.year - cand.year);

  const refSurnames = new Set((ref.authors || []).filter(Boolean).map((a) => a.toLowerCase()));
  const candSurnames = new Set((cand.authors || []).filter(Boolean).map((a) => a.toLowerCase()));
  if (refSurnames.size && candSurnames.size) {
    let hits = 0;
    for (const a of refSurnames) if (candSurnames.has(a)) hits++;
    s.author_overlap = hits / refSurnames.size;
    // the reference's lead author must appear somewhere in the candidate's
    // author list; position can differ across providers
    const lead = (ref.lead_author || '').toLowerCase();
    s.author_ok = Boolean(lead) && candSurnames.has(lead);
  }

  if (ref.corporate) {
    const pool = [cand.corporate || '', cand.title || ''].filter(Boolean).join(' ');
    s.corporate_ok = titleSimilarity(ref.corporate, cand.corporate || '') >= 0.80
      || normText(pool).includes(consortiumKey(ref.corporate));
  }

  s.journal_ok = Boolean(ref.journal)
    && journalMatch(ref.journal, cand.journal, cand.journal_abbrev);
  s.volume_ok = volumeEqual(ref.volume, cand.volume);
  s.first_page_ok = pageEqual(ref.first_page, cand.first_page);
  s.last_page_ok = pageEqual(ref.last_page, cand.last_page);

  if (ref.is_book) s.type_ok = ['book', 'bookSection'].includes(cand.item_type);
  const providers = cand.providers && cand.providers.size
    ? cand.providers : new Set([cand.provider]);
  s.n_providers = new Set([...providers].map((p) => p.split(':')[0])).size;
  s.ecitmatch = providers.has('pubmed:ecitmatch');
  return s;
}

/** Ranking score only. Never used on its own to accept a match. */
export function confidence(s) {
  let score = 0.42 * s.title_sim;
  if (s.year_delta !== null) {
    score += s.year_delta === 0 ? 0.14 : (s.year_delta === 1 ? 0.07 : 0.0);
  }
  score += s.author_ok ? 0.10 : 0.06 * s.author_overlap;
  score += s.corporate_ok ? 0.04 : 0.0;
  score += s.journal_ok ? 0.09 : 0.0;
  score += s.locator_ok ? 0.09 : ((s.volume_ok || s.first_page_ok) ? 0.04 : 0.0);
  score += s.last_page_ok ? 0.04 : 0.0;
  score += Math.min(0.05, 0.025 * (s.n_providers - 1));
  score += s.ecitmatch ? 0.03 : 0.0;
  if (!s.type_ok) score -= 0.15;
  return Math.max(0.0, Math.min(1.0, score));
}

/**
 * Hard gates. Returns [accepted, tierName, confidenceForThatTier].
 *
 * Ordered strongest-first. Each gate is a conjunction of independent signals,
 * so passing requires the candidate to agree with the reference on several
 * facts that a merely similar paper would not share.
 */
export function accept(ref, cand, s) {
  // NCBI's citation matcher keyed on journal+year+volume+first-page. Collisions
  // are essentially impossible, so a title disagreement here means the
  // *reference itself* is internally inconsistent: its locator points at one
  // paper while its title names another. That is a manuscript error, and it must
  // surface rather than resolve to whichever paper the locator happens to hit.
  if (s.ecitmatch && s.title_sim >= ECITMATCH_TITLE_GUARD) return [true, 'ecitmatch', 0.99];

  // Title + year + exact volume/first-page. The fingerprint gate.
  if (s.title_sim >= 0.90 && s.year_ok && s.locator_ok && s.type_ok) {
    return [true, 'fingerprint', 0.97];
  }

  // No locator available anywhere (older/econ records, books): demand a
  // near-exact title plus both journal and author agreement instead.
  if (s.title_sim >= 0.93 && s.year_ok && s.journal_ok
      && (s.author_ok || s.corporate_ok) && s.type_ok) {
    return [true, 'title+journal+author', 0.93];
  }

  // Independent providers converged on the same DOI.
  if (s.n_providers >= 2 && s.title_sim >= 0.88 && s.year_ok
      && (s.journal_ok || s.author_ok || s.corporate_ok) && s.type_ok) {
    return [true, 'provider-agreement', 0.90];
  }

  // Exact normalized title match with the right year and author. Used mostly
  // for books, where no provider supplies volume/page and journal is absent.
  if (normText(ref.title)
      && normText(ref.title) === normText(cand.title)
      && s.year_ok
      && (s.author_ok || s.corporate_ok)
      && s.type_ok) {
    return [true, 'exact-title', 0.88];
  }

  return [false, 'none', confidence(s)];
}

/** Separate true duplicates of the same paper (e.g. publisher DOI vs JSTOR). */
function tiebreak(cand, s) {
  const secondary = SECONDARY_DOI_PREFIXES.some((p) => (cand.doi || '').startsWith(p));
  return [
    s.last_page_ok,
    s.journal_ok,
    !secondary,
    s.n_providers,
    Boolean(cand.doi),
    Boolean(cand.pmid),
  ];
}

/** Python tuple comparison: element-wise, booleans as 0/1. */
function compareKeys(a, b) {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = typeof a[i] === 'boolean' ? (a[i] ? 1 : 0) : a[i];
    const y = typeof b[i] === 'boolean' ? (b[i] ? 1 : 0) : b[i];
    if (x !== y) return x < y ? -1 : 1;
  }
  return a.length - b.length;
}

export function rank(ref, cands) {
  const scored = cands.map((c) => {
    const sg = signals(ref, c);
    return [c, sg, confidence(sg)];
  });
  // Python's `sort(key=..., reverse=True)` is a *stable descending* sort, so
  // equally-scored candidates keep the order the providers returned them in.
  // Returning 0 for ties preserves that, because Array.prototype.sort is
  // required to be stable.
  scored.sort((p, q) => {
    const keyP = [p[2], tiebreak(p[0], p[1])];
    const keyQ = [q[2], tiebreak(q[0], q[1])];
    if (keyP[0] !== keyQ[0]) return keyP[0] < keyQ[0] ? 1 : -1;
    return -compareKeys(keyP[1], keyQ[1]);
  });
  return scored;
}

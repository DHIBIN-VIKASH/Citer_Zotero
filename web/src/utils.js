/**
 * Port of zotprep/utils.py — normalization and comparison primitives.
 *
 * The accept/reject decision is built entirely out of these functions, so this
 * file and fuzz.js are where behavioural parity with the Python engine has to
 * be exact. Every difference between Python's `re`/`str` and JavaScript's
 * `RegExp`/`String` that matters here is called out inline:
 *
 *   - Python's `\w`, `\b` and `str.isupper()` are Unicode-aware; JavaScript's
 *     `\w` and `\b` are ASCII-only. Where the Python pattern relies on that,
 *     the port uses explicit `\p{...}` classes with the `u` flag.
 *   - `str.strip(chars)` removes any of several characters from both ends;
 *     `String.trim()` only removes whitespace. Hence `stripChars` below.
 *   - `unicodedata.combining(c)` is replaced by the `\p{M}` category, which is
 *     what NFD decomposition produces.
 *
 * Verified against the Python original by tools/parity/utils_parity.py.
 */
import { tokenSetRatio, tokenSortRatio } from './fuzz.js';

// Words that carry no discriminating power in a journal title.
export const JOURNAL_STOPWORDS = new Set(
  ['the', 'of', 'and', 'for', 'in', 'on', 'a', 'an', 'de', 'der', 'la', 'le'],
);
export const TITLE_STOPWORDS = new Set(['the', 'a', 'an']);

/**
 * Python's `str.strip(chars)`: strip any of `chars` from both ends.
 * Called with " .,;" in the extractor, where `String.trim()` would be wrong.
 */
export function stripChars(s, chars) {
  let start = 0;
  let end = s.length;
  while (start < end && chars.includes(s[start])) start++;
  while (end > start && chars.includes(s[end - 1])) end--;
  return s.slice(start, end);
}

/** Python's `str.rstrip(chars)`. */
export function rstripChars(s, chars) {
  let end = s.length;
  while (end > 0 && chars.includes(s[end - 1])) end--;
  return s.slice(0, end);
}

/**
 * Drop combining marks after NFD, i.e. unicodedata NFD + `not combining(c)`.
 * `\p{M}` is exactly the set of characters `unicodedata.combining` reports as
 * non-zero for the output of NFD.
 */
export function stripAccents(s) {
  return String(s ?? '').normalize('NFD').replace(/\p{M}+/gu, '');
}

/** Aggressive normalization for fuzzy title comparison. */
export function normText(s) {
  let t = stripAccents(String(s ?? '').normalize('NFKC')).toLowerCase();
  t = t.replace(/<[^>]+>/g, ' '); // provider titles sometimes carry <i>/<sub>
  t = t.replace(/&(amp|lt|gt|quot|apos);/g, ' ');
  t = t.replace(/[^a-z0-9]+/g, ' ');
  return t.split(/\s+/).filter((x) => x && !TITLE_STOPWORDS.has(x)).join(' ');
}

/**
 * 0..1 similarity between two titles.
 *
 * `token_set_ratio` alone is unsafe here: it scores 1.00 whenever one title's
 * tokens are a subset of the other's, so a short reference title matches a
 * longer unrelated paper perfectly. The Python original documents at length why
 * no length penalty is applied — legitimate matches are also subsets, because
 * journals expand titles in the version of record. The inflation is handled by
 * the accept gate never taking title similarity on its own, and that division
 * of labour is preserved here.
 */
export function titleSimilarity(a, b) {
  const na = normText(a);
  const nb = normText(b);
  if (!na || !nb) return 0.0;
  if (na === nb) return 1.0;
  return Math.max(tokenSetRatio(na, nb), tokenSortRatio(na, nb)) / 100.0;
}

export function normDoi(doi) {
  if (!doi) return null;
  let d = String(doi).trim().toLowerCase();
  d = d.replace(/^https?:\/\/(dx\.)?doi\.org\//, '');
  d = d.replace(/^doi:\s*/, '');
  d = rstripChars(d, '.');
  return d || null;
}

export function normSurname(s) {
  let t = stripAccents(s || '').toLowerCase();
  t = t.replace(/[^a-z\s-]/g, '');
  return t.replace(/\s+/g, ' ').trim();
}

/**
 * Best-effort surname from a provider's free-form author name.
 * Handles "Prakash C Gupta", "Gupta, Prakash C", and particle surnames
 * ("Xavier Sala-i-Martin", "Jacques Vallin", "Ludwig van Beethoven").
 */
export function surnameOf(displayName) {
  const name = String(displayName ?? '').trim();
  if (!name) return '';
  if (name.includes(',')) return normSurname(name.split(',')[0]);
  const parts = name.split(/\s+/);
  if (parts.length === 1) return normSurname(parts[0]);
  // walk back over lowercase particles: "van der Berg" -> "van der berg".
  // Python's str.islower() on a one-character slice is Unicode-aware, so a
  // lowercase accented particle ("île") counts; \p{Ll} preserves that.
  let i = parts.length - 1;
  while (i > 0 && /^\p{Ll}/u.test(parts[i - 1].slice(0, 1))) i--;
  return normSurname(parts.slice(i).join(' '));
}

function journalTokens(s) {
  let t = stripAccents(s || '').toLowerCase();
  t = t.replace(/[^a-z0-9]+/g, ' ');
  return t.split(/\s+/).filter((x) => x && !JOURNAL_STOPWORDS.has(x));
}

/**
 * Abbreviation-tolerant journal comparison, no lookup table needed.
 *
 * Vancouver uses NLM-style abbreviations where every abbreviated token is a
 * prefix of the corresponding full word, in order:
 *
 *     "J Polit Econ"            ~ "Journal of Political Economy"
 *     "J R Stat Soc Series B"   ~ "Journal of the Royal Statistical Society: Series B"
 *     "Bull World Health Organ" ~ "Bulletin of the World Health Organization"
 *
 * Token counts must match exactly. Without that, "Lancet" would match "Lancet
 * Oncology" — a wrong-journal false positive is far more costly than a missed
 * signal, since the accept gate can fall back to the volume/page fingerprint.
 */
export function journalMatch(refJournal, ...candidates) {
  const refToks = journalTokens(refJournal);
  if (!refToks.length) return false;
  for (const cand of candidates) {
    if (!cand) continue;
    // try the raw name, then progressively stripped variants, because
    // providers append qualifiers: "… Society: Series B (Methodological)"
    const variants = [cand, cand.replace(/\([^)]*\)/g, ' ')];
    if (cand.includes(':')) variants.push(cand.split(':')[0]);
    for (const variant of variants) {
      const candToks = journalTokens(variant);
      if (candToks.length !== refToks.length) continue;
      if (refToks.every((r, i) => candToks[i].startsWith(r) || r.startsWith(candToks[i]))) {
        return true;
      }
    }
  }
  return false;
}

function normLocator(x) {
  return stripAccents(x).toLowerCase().replace(/[^a-z0-9]/g, '');
}

/** Compare page labels tolerantly: 'e1339' == 'e1339', '17' == '17-23' start. */
export function pageEqual(a, b) {
  if (!a || !b) return false;
  return normLocator(a) === normLocator(b);
}

export function volumeEqual(a, b) {
  if (!a || !b) return false;
  return normLocator(a) === normLocator(b);
}

/**
 * A faithful JavaScript port of the three `rapidfuzz.fuzz` functions the
 * resolver depends on: `ratio`, `token_sort_ratio` and `token_set_ratio`.
 *
 * This file is the single highest-risk part of the browser port. Every accept
 * gate in scorer.js compares a title similarity against a hard threshold
 * (0.88 / 0.90 / 0.92 / 0.93), so a value that is merely *close* to the Python
 * one is not good enough — a disagreement in the last float bit can flip an
 * accept into a REVIEW. The implementation is therefore written to reproduce
 * rapidfuzz's arithmetic exactly rather than to be idiomatic:
 *
 *   - similarity is `(1 - dist/lensum) * 100`, NOT the algebraically equal
 *     `2*lcs/lensum`. In IEEE-754 doubles those differ: for ("abc","abd")
 *     rapidfuzz returns 66.66666666666667 and `2*lcs/lensum` gives
 *     66.66666666666666.
 *   - tokens are sorted by code point (Python `sorted`), not by UTF-16 code
 *     unit (JavaScript's default `Array.prototype.sort`).
 *   - `.split()` with no argument in Python splits on runs of whitespace and
 *     discards empty fields; `String.split(/\s+/)` does not, hence the trim.
 *
 * Equivalence is not asserted here, it is tested: tools/parity/fuzz_parity.py
 * runs this file and the real rapidfuzz over the same corpus and fails on any
 * difference in the exact double.
 */

/** Python's `sorted()` on str: ascending by Unicode code point. */
function byCodePoint(a, b) {
  const ai = Array.from(a);
  const bi = Array.from(b);
  const n = Math.min(ai.length, bi.length);
  for (let i = 0; i < n; i++) {
    const x = ai[i].codePointAt(0);
    const y = bi[i].codePointAt(0);
    if (x !== y) return x < y ? -1 : 1;
  }
  return ai.length - bi.length;
}

/** Python's `str.split()` with no argument. */
export function splitTokens(s) {
  const t = String(s ?? '').trim();
  return t ? t.split(/\s+/) : [];
}

/**
 * Decompose to Unicode code points.
 *
 * Python indexes and measures `str` by code point; JavaScript does both by
 * UTF-16 code unit. For anything outside the BMP the two disagree — "𝒜" is one
 * character to Python and two to `String.prototype.length` — which would shift
 * every length, distance and similarity involving it. Titles reaching the
 * scorer are ASCII by the time norm_text() is done with them, so this cannot
 * bite in production, but the metric is defined on code points and is
 * implemented that way rather than relying on that.
 */
function codePoints(s) {
  const n = s.length;
  const out = new Int32Array(n);
  let k = 0;
  for (let i = 0; i < n; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < n) {
      const d = s.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) {
        out[k++] = (c - 0xd800) * 0x400 + (d - 0xdc00) + 0x10000;
        i++;
        continue;
      }
    }
    out[k++] = c;
  }
  return k === n ? out : out.subarray(0, k);
}

/**
 * Length of the longest common subsequence of two code point arrays.
 *
 * The Indel (insert/delete only) edit distance rapidfuzz uses is
 * `len1 + len2 - 2*lcs`, so this is the only quantity that needs computing.
 * Rolling one-row DP: O(n*m) time, O(min(n,m)) space. rapidfuzz uses a
 * bit-parallel variant for speed, but LCS length is LCS length — the result is
 * identical, and titles are short enough that the difference does not matter.
 */
function lcsCodes(a, b) {
  if (!a.length || !b.length) return 0;
  // iterate over the longer sequence so the row is the shorter one
  if (a.length < b.length) { const t = a; a = b; b = t; }
  const m = b.length;
  const row = new Int32Array(m + 1);
  for (let i = 0; i < a.length; i++) {
    const ai = a[i];
    let prevDiag = 0; // row[j-1] from the previous i iteration
    for (let j = 1; j <= m; j++) {
      const tmp = row[j];
      row[j] = ai === b[j - 1]
        ? prevDiag + 1
        : (row[j] >= row[j - 1] ? row[j] : row[j - 1]);
      prevDiag = tmp;
    }
  }
  return row[m];
}

export function lcsLength(a, b) {
  return lcsCodes(codePoints(a), codePoints(b));
}

export function indelDistance(a, b) {
  const ca = codePoints(a);
  const cb = codePoints(b);
  return ca.length + cb.length - 2 * lcsCodes(ca, cb);
}

/**
 * rapidfuzz.fuzz.ratio — normalized Indel similarity, scaled to 0..100.
 *
 * Note the exact expression: `(1 - dist/lensum) * 100`. token_set_ratio below
 * uses the algebraically identical `100 - 100*dist/lensum`, which is NOT the
 * same double — for dist=2, lensum=6 this gives 66.66666666666667 and that one
 * gives 66.66666666666666. Both forms are reproduced where rapidfuzz uses them.
 *
 * Two empty strings score 100, matching rapidfuzz.
 */
export function ratio(a, b) {
  const ca = codePoints(a);
  const cb = codePoints(b);
  const lensum = ca.length + cb.length;
  if (lensum === 0) return 100.0;
  const dist = ca.length + cb.length - 2 * lcsCodes(ca, cb);
  return (1.0 - dist / lensum) * 100.0;
}

/** rapidfuzz.fuzz.token_sort_ratio — sort the tokens, then `ratio`. */
export function tokenSortRatio(a, b) {
  const sa = splitTokens(a).sort(byCodePoint).join(' ');
  const sb = splitTokens(b).sort(byCodePoint).join(' ');
  return ratio(sa, sb);
}

/**
 * Normalized distance as token_set_ratio scores it. Written `100 - 100*d/l`,
 * matching rapidfuzz — see the note on ratio() above; the other spelling of
 * this formula produces a different double.
 */
function normDistance(dist, lensum) {
  return lensum ? 100.0 - (100.0 * dist) / lensum : 100.0;
}

/** Code point length, matching Python's len() on str. */
function cpLength(s) {
  return codePoints(s).length;
}

/**
 * rapidfuzz.fuzz.token_set_ratio.
 *
 * Compare the shared tokens against each side's full token set, and the two
 * sides' private tokens against each other; the best of the three wins. This is
 * why the metric returns 100 whenever one title's tokens are a subset of the
 * other's — the behaviour utils.js documents and the accept gate compensates
 * for, so it is reproduced here rather than "fixed".
 *
 * Two details are carried over from rapidfuzz deliberately, because a
 * "cleaner" formulation gives different numbers:
 *
 *   - the main term is the Indel distance of the two *private* token strings,
 *     but normalized by the length of the two *full* strings (shared + private).
 *     The shared prefix contributes no distance, so this is equivalent to
 *     comparing the full strings — and much cheaper — but only if the length
 *     sum keeps counting it.
 *   - the shared-vs-full ratios are computed from length arithmetic rather than
 *     an actual edit distance, since the shared part is a prefix of both.
 *
 * Empty token sets return 0, not 100. rapidfuzz notes this is FuzzyWuzzy
 * compatibility (RapidFuzz issue #110) — a deliberate inconsistency with
 * `ratio("", "") == 100`, and one the accept gate would notice.
 */
export function tokenSetRatio(a, b) {
  const ta = new Set(splitTokens(a));
  const tb = new Set(splitTokens(b));
  if (!ta.size || !tb.size) return 0.0;

  const intersect = [];
  const diffAB = [];
  const diffBA = [];
  for (const t of ta) (tb.has(t) ? intersect : diffAB).push(t);
  for (const t of tb) if (!ta.has(t)) diffBA.push(t);

  // one sentence is part of the other one
  if (intersect.length && (!diffAB.length || !diffBA.length)) return 100.0;

  const abJoined = diffAB.sort(byCodePoint).join(' ');
  const baJoined = diffBA.sort(byCodePoint).join(' ');
  const abLen = cpLength(abJoined);
  const baLen = cpLength(baJoined);
  // order does not affect the joined length, so the unsorted set is fine here
  const sectLen = cpLength(intersect.join(' '));

  const hasSect = sectLen !== 0 ? 1 : 0;
  const sectAbLen = sectLen + hasSect + abLen;
  const sectBaLen = sectLen + hasSect + baLen;

  const dist = indelDistance(abJoined, baJoined);
  let result = normDistance(dist, sectAbLen + sectBaLen);

  // the other two ratios are 0 without a shared part
  if (!sectLen) return result;

  const sectAbRatio = normDistance(hasSect + abLen, sectLen + sectAbLen);
  const sectBaRatio = normDistance(hasSect + baLen, sectLen + sectBaLen);
  if (sectAbRatio > result) result = sectAbRatio;
  if (sectBaRatio > result) result = sectBaRatio;
  return result;
}

export default { ratio, tokenSortRatio, tokenSetRatio, lcsLength, indelDistance, splitTokens };

/**
 * JS side of the utils parity harness.
 *
 * Reads {"calls": [{"fn": name, "args": [...]}, ...]} on stdin and writes a
 * tagged result per call. Values are tagged by type and floats are emitted as
 * IEEE-754 bit patterns, so a Python `0.9` and a JS `0.9` can only compare
 * equal if they really are the same double — and so a `false` can never be
 * mistaken for a `0` or a `""` across the JSON boundary.
 */
import * as U from '../../src/utils.js';

const buf = new ArrayBuffer(8);
const f64 = new Float64Array(buf);
const u8 = new Uint8Array(buf);

function bits(x) {
  f64[0] = x;
  let out = '';
  for (let i = 7; i >= 0; i--) out += u8[i].toString(16).padStart(2, '0');
  return out;
}

function tag(v) {
  if (v === null || v === undefined) return { t: 'n' };
  if (typeof v === 'boolean') return { t: 'b', v };
  if (typeof v === 'number') return { t: 'f', v: bits(v) };
  if (typeof v === 'string') return { t: 's', v };
  if (Array.isArray(v)) return { t: 'a', v: v.map(tag) };
  return { t: '?', v: String(v) };
}

const FNS = {
  strip_accents: (s) => U.stripAccents(s),
  norm_text: (s) => U.normText(s),
  title_similarity: (a, b) => U.titleSimilarity(a, b),
  norm_doi: (s) => U.normDoi(s),
  norm_surname: (s) => U.normSurname(s),
  surname_of: (s) => U.surnameOf(s),
  journal_match: (ref, ...cands) => U.journalMatch(ref, ...cands),
  page_equal: (a, b) => U.pageEqual(a, b),
  volume_equal: (a, b) => U.volumeEqual(a, b),
  strip_chars: (s, c) => U.stripChars(s, c),
  rstrip_chars: (s, c) => U.rstripChars(s, c),
};

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => { input += c; });
process.stdin.on('end', () => {
  const { calls } = JSON.parse(input);
  const out = calls.map(({ fn, args }) => {
    try {
      return tag(FNS[fn](...args));
    } catch (e) {
      return { t: 'e', v: String(e && e.message) };
    }
  });
  process.stdout.write(JSON.stringify(out));
});

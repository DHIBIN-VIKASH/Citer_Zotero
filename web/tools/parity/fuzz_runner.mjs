/**
 * JS side of the fuzz parity harness.
 *
 * Reads {"pairs": [[a, b], ...]} on stdin, writes one result object per pair on
 * stdout. Scores are emitted as raw IEEE-754 bit patterns, not decimal text, so
 * the comparison against Python is exact and cannot be blurred by either side's
 * float formatting.
 */
import { ratio, tokenSortRatio, tokenSetRatio } from '../../src/fuzz.js';

const buf = new ArrayBuffer(8);
const f64 = new Float64Array(buf);
const u8 = new Uint8Array(buf);

function bits(x) {
  f64[0] = x;
  let out = '';
  for (let i = 7; i >= 0; i--) out += u8[i].toString(16).padStart(2, '0');
  return out;
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => { input += c; });
process.stdin.on('end', () => {
  const { pairs } = JSON.parse(input);
  const out = pairs.map(([a, b]) => ({
    ratio: bits(ratio(a, b)),
    token_sort_ratio: bits(tokenSortRatio(a, b)),
    token_set_ratio: bits(tokenSetRatio(a, b)),
  }));
  process.stdout.write(JSON.stringify(out));
});

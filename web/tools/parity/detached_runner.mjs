/**
 * JS side of the detached-citation parity harness.
 *
 * Reads {"calls": [{"fn": "detached_spans", "args": [text]}, ...]} on stdin and
 * writes one tagged result per call. Spans are compared as [start, end, spec]
 * triples: a rule that finds the right digits at the wrong offset would rewrite
 * the wrong part of the sentence, so the offsets are part of the contract.
 */
import { detachedSpans } from '../../src/docx.js';

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
  detached_spans: (text) => detachedSpans(text).map(([s, e, spec]) => [s, e, spec]),
};

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (d) => { input += d; });
process.stdin.on('end', () => {
  const { calls } = JSON.parse(input);
  process.stdout.write(JSON.stringify(calls.map(({ fn, args }) => tag(FNS[fn](...args)))));
});

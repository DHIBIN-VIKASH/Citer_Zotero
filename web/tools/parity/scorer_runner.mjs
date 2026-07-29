/**
 * JS side of the scorer parity harness.
 *
 * Reads serialized ParsedRef/Candidate pairs and emits every observable of the
 * scoring layer: each Signals field, the derived properties, the failure
 * strings, the confidence float, the accept tuple, and the full ranking order.
 * Floats go out as IEEE-754 bit patterns so the comparison is exact.
 */
import { ParsedRef, Candidate } from '../../src/models.js';
import { signals, confidence, accept, rank } from '../../src/scorer.js';

const buf = new ArrayBuffer(8);
const f64 = new Float64Array(buf);
const u8 = new Uint8Array(buf);
function bits(x) {
  f64[0] = x;
  let out = '';
  for (let i = 7; i >= 0; i--) out += u8[i].toString(16).padStart(2, '0');
  return out;
}

function toRef(d) {
  const r = new ParsedRef(d.n ?? 1, d.raw ?? '');
  for (const k of Object.keys(d)) if (k in r) r[k] = d[k];
  r.authors = d.authors || [];
  return r;
}

function toCand(d) {
  return new Candidate({ ...d, providers: new Set(d.providers || []) });
}

const SIGNAL_FIELDS = [
  'title_sim', 'year_delta', 'author_ok', 'author_overlap', 'corporate_ok',
  'journal_ok', 'volume_ok', 'first_page_ok', 'last_page_ok', 'type_ok',
  'n_providers', 'ecitmatch',
];

function dumpSignals(s) {
  const out = {};
  for (const f of SIGNAL_FIELDS) {
    const v = s[f];
    out[f] = typeof v === 'number' ? bits(v) : v;
  }
  out.locator_ok = s.locator_ok;
  out.year_ok = s.year_ok;
  out.failing = s.failing();
  return out;
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => { input += c; });
process.stdin.on('end', () => {
  const { pairs, rankings } = JSON.parse(input);

  const scored = pairs.map(({ ref, cand }) => {
    const r = toRef(ref);
    const c = toCand(cand);
    const s = signals(r, c);
    const [ok, tier, conf] = accept(r, c, s);
    return {
      signals: dumpSignals(s),
      confidence: bits(confidence(s)),
      accept: [ok, tier, bits(conf)],
    };
  });

  const ranked = rankings.map(({ ref, cands }) => {
    const r = toRef(ref);
    const cs = cands.map(toCand);
    // tag each candidate so the resulting order can be compared by identity
    cs.forEach((c, i) => { c.__i = i; });
    return rank(r, cs).map(([c, , conf]) => [c.__i, bits(conf)]);
  });

  process.stdout.write(JSON.stringify({ scored, ranked }));
});

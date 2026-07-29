/**
 * JS side of the extractor parity harness: raw reference strings in, the full
 * ParsedRef field set out, in a form the Python side can compare exactly.
 */
import { parseReference, nfkc, expandPage } from '../../src/extractor.js';

const FIELDS = [
  'doi', 'pmid', 'pmcid', 'authors', 'corporate', 'title', 'journal', 'year',
  'volume', 'issue', 'first_page', 'last_page', 'is_book', 'is_chapter',
  'book_title', 'publisher', 'place', 'edition',
];

function dump(ref) {
  const out = {};
  for (const f of FIELDS) {
    const v = ref[f];
    out[f] = v === undefined ? null : v;
  }
  out.has_identifier = ref.has_identifier;
  out.lead_author = ref.lead_author;
  return out;
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => { input += c; });
process.stdin.on('end', () => {
  const { refs, nfkcCases, pageCases } = JSON.parse(input);
  process.stdout.write(JSON.stringify({
    parsed: refs.map((raw, i) => {
      try {
        return dump(parseReference(i + 1, raw));
      } catch (e) {
        return { error: String(e && e.message) };
      }
    }),
    nfkc: (nfkcCases || []).map((s) => nfkc(s)),
    pages: (pageCases || []).map(([a, b]) => expandPage(a, b)),
  }));
});

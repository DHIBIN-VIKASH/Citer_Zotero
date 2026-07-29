/**
 * Port of zotprep/search/crossref.py.
 *
 * Used with `query.title` + `query.author` rather than `query.bibliographic`.
 * The old code's use of query.bibliographic on the whole reference string is
 * precisely what produced wrong #1 hits: journal abbreviations and page ranges
 * become query noise that Crossref's relevance scorer happily matches against
 * other papers.
 *
 * Crossref is also the metadata source of record once a DOI is known, so
 * `byDoi` is what fills in publisher/pages/ISSN for the Zotero item.
 */
import { Candidate } from '../models.js';
import { surnameOf } from '../utils.js';
import { get, queryTerms } from './base.js';

export const API = 'https://api.crossref.org/works';

// Only fields Crossref actually accepts in `select`. An unsupported name makes
// the whole request a 400, silently zeroing out this provider — keep this list
// in sync with https://api.crossref.org/works?rows=0 and do not add
// speculatively.
const SELECT = 'DOI,title,author,issued,container-title,short-container-title,'
  + 'volume,issue,page,type,publisher,publisher-location,ISSN';

const TYPE_MAP = {
  'journal-article': 'journalArticle',
  book: 'book',
  monograph: 'book',
  'edited-book': 'book',
  'reference-book': 'book',
  'book-chapter': 'bookSection',
  'book-section': 'bookSection',
  'proceedings-article': 'conferencePaper',
  'posted-content': 'preprint',
  report: 'report',
  dissertation: 'thesis',
  dataset: 'dataset',
};

/** Python's str.partition("-"): split on the FIRST hyphen only. */
export function partition(s, sep) {
  const i = (s || '').indexOf(sep);
  if (i < 0) return [s || '', '', ''];
  return [s.slice(0, i), sep, s.slice(i + sep.length)];
}

export function toCandidate(w) {
  const authors = [];
  let corporate = null;
  for (const a of (w.author || []).slice(0, 60)) {
    if (a.family) {
      authors.push(surnameOf(`${a.given || ''} ${a.family}`.trim()));
    } else if (a.name) {
      corporate = corporate || a.name;
    }
  }
  const dp = ((w.issued || {})['date-parts'] || [[null]])[0] || [null];
  const page = w.page || '';
  const [fp, , lp] = partition(page, '-');
  const ct = w['container-title'] || [];
  const sct = w['short-container-title'] || [];
  const itemType = TYPE_MAP[w.type || ''] || 'journalArticle';
  // For a chapter, Crossref's container-title is the *book* title, which Zotero
  // needs in its own bookTitle field rather than as a publication title.
  const bookTitle = (ct.length && itemType === 'bookSection') ? ct[0] : '';
  return new Candidate({
    provider: 'crossref',
    title: (w.title && w.title.length) ? (w.title[0] || '') : '',
    doi: w.DOI,
    authors,
    corporate,
    year: Number.isInteger(dp[0]) ? dp[0] : null,
    journal: ct.length ? ct[0] : '',
    journal_abbrev: sct.length ? sct[0] : '',
    volume: w.volume,
    issue: w.issue,
    first_page: fp.trim() || null,
    last_page: lp.trim() || null,
    item_type: itemType,
    book_title: bookTitle,
    publisher: w.publisher,
    place: w['publisher-location'],
    raw: w,
  });
}

export async function search(ref, mailto, opts = {}) {
  const [title, author] = queryTerms(ref);
  if (!title) return [];
  const params = {
    'query.title': title, rows: 10, select: SELECT, mailto,
  };
  if (author) params['query.author'] = author;
  let data = await get('crossref', API, { params, ...opts });
  let items = ((data || {}).message || {}).items || [];

  if (!items.length) {
    // Last resort only: bibliographic search over the reconstructed citation.
    // Deliberately not the primary query — its relevance ranking is what made
    // the previous version return confidently wrong papers.
    const recon = [title, ref.journal, String(ref.year || ''), ref.volume, ref.first_page]
      .filter(Boolean).join(' ');
    data = await get('crossref', API, {
      params: {
        'query.bibliographic': recon, rows: 8, select: SELECT, mailto,
      },
      ...opts,
    });
    items = ((data || {}).message || {}).items || [];
  }
  return items.map(toCandidate);
}

export async function byDoi(doi, mailto, opts = {}) {
  const data = await get('crossref', `${API}/${encodeURIComponent(doi)}`, { params: { mailto }, ...opts });
  const msg = (data || {}).message;
  return msg ? toCandidate(msg) : null;
}

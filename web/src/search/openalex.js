/**
 * Port of zotprep/search/openalex.py.
 *
 * The most valuable single provider for this manuscript's mix: it indexes the
 * economics journals PubMed has never heard of, it indexes books, and crucially
 * it returns `biblio.{volume, issue, first_page, last_page}` — the fingerprint
 * the scorer needs to prove a match rather than guess at one.
 */
import { Candidate } from '../models.js';
import { surnameOf } from '../utils.js';
import { get, queryTerms } from './base.js';

export const API = 'https://api.openalex.org/works';

// OpenAlex maps its own work types onto Zotero item types.
const TYPE_MAP = {
  article: 'journalArticle',
  'journal-article': 'journalArticle',
  book: 'book',
  'book-chapter': 'bookSection',
  monograph: 'book',
  report: 'report',
  dissertation: 'thesis',
  preprint: 'preprint',
  'proceedings-article': 'conferencePaper',
  paratext: 'journalArticle',
  editorial: 'journalArticle',
  letter: 'journalArticle',
  review: 'journalArticle',
};

// Python's \b(...)\b with re.I. Written with explicit Unicode boundaries for the
// same reason as in extractor.js: JavaScript's \b is ASCII-only.
const CONSORTIUM_RE = /(?<![\p{L}\p{N}_])(collaborators?|group|consortium|committee|network)(?![\p{L}\p{N}_])/iu;

function toCandidate(w) {
  const biblio = w.biblio || {};
  const src = ((w.primary_location || {}).source) || {};
  const ids = w.ids || {};
  let pmid = ids.pmid;
  if (pmid) pmid = pmid.replace(/\/+$/, '').split('/').pop();
  const authors = [];
  let corporate = null;
  for (const a of (w.authorships || []).slice(0, 60)) {
    const nm = ((a.author || {}).display_name) || a.raw_author_name || '';
    if (!nm) continue;
    // OpenAlex sometimes stores consortium names in the author slot
    if (CONSORTIUM_RE.test(nm)) {
      corporate = corporate || nm;
      continue;
    }
    authors.push(surnameOf(nm));
  }
  const host = src.host_organization_name || '';
  const wtype = (w.type || '').toLowerCase();
  return new Candidate({
    provider: 'openalex',
    title: w.title || w.display_name || '',
    doi: w.doi,
    pmid,
    authors,
    corporate,
    year: w.publication_year,
    journal: src.display_name || '',
    journal_abbrev: src.abbreviated_title || '',
    volume: biblio.volume,
    issue: biblio.issue,
    first_page: biblio.first_page,
    last_page: biblio.last_page,
    item_type: TYPE_MAP[wtype] || 'journalArticle',
    publisher: ['book', 'monograph', 'book-chapter'].includes(wtype) ? host : null,
    raw: w,
  });
}

/** OpenAlex filter syntax uses , | : + as operators, so strip them out. */
function sanitizeFilterValue(s) {
  return (s || '').replace(/[,|:+()[\]{}]/g, ' ').replace(/\s+/g, ' ').trim();
}

export async function search(ref, mailto, opts = {}) {
  const [title, author] = queryTerms(ref);
  if (!title) return [];
  const out = [];
  const seen = new Set();

  // Pass 1: title-field search — high precision.
  const data = await get('openalex', API, {
    params: {
      filter: `title.search:${sanitizeFilterValue(title)}`,
      'per-page': 10,
      mailto,
    },
    ...opts,
  });
  // Pass 2 only when pass 1 came back empty. OpenAlex is metered now, so every
  // avoidable request is a request the rest of the bibliography gets to use.
  let data2 = null;
  if (!((data || {}).results || []).length) {
    data2 = await get('openalex', API, {
      params: { search: `${title} ${author}`.trim(), 'per-page': 10, mailto },
      ...opts,
    });
  }
  for (const d of [data, data2]) {
    for (const w of ((d || {}).results || [])) {
      const wid = w.id || '';
      if (seen.has(wid)) continue;
      seen.add(wid);
      out.push(toCandidate(w));
    }
  }
  return out;
}

export async function byIdentifier(mailto, { doi = null, pmid = null } = {}, opts = {}) {
  const ident = doi ? `doi:${doi}` : (pmid ? `pmid:${pmid}` : null);
  if (!ident) return null;
  const data = await get('openalex', `${API}/${ident}`, { params: { mailto }, ...opts });
  return data ? toCandidate(data) : null;
}

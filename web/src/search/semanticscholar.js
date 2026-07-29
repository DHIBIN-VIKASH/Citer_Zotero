/**
 * Port of zotprep/search/semanticscholar.py.
 *
 * Third vote for cross-provider agreement, and decent coverage of economics and
 * older monographs. The unauthenticated endpoint is aggressively rate limited,
 * so this provider is treated as strictly optional: a 429 storm degrades it to
 * returning nothing, which costs a vote but never blocks a resolution.
 *
 * Opt-in, exactly as in the CLI. There the switch is $SEMANTIC_SCHOLAR_API_KEY;
 * here it is a field in the settings panel. Without a key this endpoint 429s on
 * nearly every call, and its 1.1s pacing serialises the whole run for a provider
 * that contributes nothing — so it stays wired up but stays off until there is
 * one.
 */
import { Candidate } from '../models.js';
import { surnameOf } from '../utils.js';
import { get, queryTerms } from './base.js';
import { partition } from './crossref.js';

export const API = 'https://api.semanticscholar.org/graph/v1/paper/search';
const FIELDS = 'title,year,venue,externalIds,authors,publicationVenue,journal,publicationTypes';

const TYPE_MAP = { Book: 'book', BookSection: 'bookSection', Conference: 'conferencePaper' };

function toCandidate(p) {
  const ext = p.externalIds || {};
  const journal = p.journal || {};
  const venueObj = p.publicationVenue || {};
  const [fp, , lp] = partition(journal.pages || '', '-');
  const ptypes = p.publicationTypes || [];
  const matched = ptypes.find((t) => t in TYPE_MAP);
  return new Candidate({
    provider: 'semanticscholar',
    title: p.title || '',
    doi: ext.DOI,
    pmid: ext.PubMed ? String(ext.PubMed) : null,
    authors: (p.authors || []).slice(0, 60).filter((a) => a.name).map((a) => surnameOf(a.name)),
    year: p.year,
    journal: journal.name || venueObj.name || p.venue || '',
    journal_abbrev: venueObj && Object.keys(venueObj).length
      ? ((venueObj.alternate_names || [''])[0] || '') : '',
    volume: (journal.volume || '').trim() || null,
    first_page: fp.trim() || null,
    last_page: lp.trim() || null,
    item_type: matched ? TYPE_MAP[matched] : 'journalArticle',
    raw: p,
  });
}

export async function search(ref, _mailto, opts = {}, apiKey = '') {
  if (!apiKey) return [];
  const [title, author] = queryTerms(ref);
  if (!title) return [];
  const data = await get('semanticscholar', API, {
    params: { query: `${title} ${author}`.trim(), limit: 8, fields: FIELDS },
    headers: { 'x-api-key': apiKey },
    attempts: 3,
    ...opts,
  });
  return ((data || {}).data || []).map(toCandidate);
}

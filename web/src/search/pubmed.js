/**
 * Port of zotprep/search/pubmed.py — deterministic first, free-text second.
 *
 * The important piece is `ecitmatch`, NCBI's citation matcher. Given
 * journal|year|volume|first_page|author it returns the exact PMID or NOT_FOUND.
 * Because Vancouver references already carry NLM journal abbreviations, volume
 * and first page, this turns most biomedical references into a *lookup* rather
 * than a search — no relevance ranking, no ambiguity, nothing to score.
 *
 * It also works with an empty author field, which is what rescues the consortium
 * references (GBD / India State-Level Collaborators) whose "first author" is not
 * a personal surname at all.
 */
import { Candidate } from '../models.js';
import { surnameOf } from '../utils.js';
import { get } from './base.js';
import { partition } from './crossref.js';

export const EUTILS = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils';
// NCBI's `tool` parameter, which identifies the client in their logs. The CLI
// sends "zotprep"; this is a different application talking to the same API, so
// it says so. Purely identification — it does not affect any result.
const TOOL = 'z-link';

const COLLECTIVE_RE = /(?<![\p{L}\p{N}_])(collaborators?|group|consortium|committee|network)(?![\p{L}\p{N}_])/iu;
const YEAR_RE = /(?<![\p{L}\p{N}_])(19|20)\d{2}(?![\p{L}\p{N}_])/u;

function toCandidate(rec) {
  const authors = [];
  let corporate = null;
  for (const a of (rec.authors || [])) {
    const nm = a.name || '';
    if (!nm) continue;
    if (a.authtype === 'CollectiveName' || COLLECTIVE_RE.test(nm)) {
      corporate = corporate || nm;
      continue;
    }
    authors.push(surnameOf(nm));
  }
  const doiEntry = (rec.articleids || []).find((i) => i.idtype === 'doi');
  const ym = YEAR_RE.exec(rec.pubdate || '');
  const [fp, , lp] = partition(rec.pages || '', '-');
  return new Candidate({
    provider: 'pubmed',
    title: (rec.title || '').replace(/\.+$/, ''),
    doi: doiEntry ? doiEntry.value : null,
    pmid: String(rec.uid || '') || null,
    authors,
    corporate,
    year: ym ? parseInt(ym[0], 10) : null,
    journal: rec.fulljournalname || rec.source || '',
    journal_abbrev: rec.source || '',
    volume: rec.volume || null,
    issue: rec.issue || null,
    first_page: fp.trim() || null,
    last_page: lp.trim() || null,
    raw: rec,
  });
}

async function summaries(pmids, email, opts = {}) {
  if (!pmids.length) return [];
  const data = await get('pubmed', `${EUTILS}/esummary.fcgi`, {
    params: {
      db: 'pubmed', id: pmids.join(','), retmode: 'json', tool: TOOL, email,
    },
    ...opts,
  });
  const result = (data || {}).result || {};
  return pmids
    .filter((p) => result[p] && typeof result[p] === 'object')
    .map((p) => toCandidate(result[p]));
}

/**
 * Deterministic PMID lookup from the reference's own locator fields.
 *
 * Tries with the first author and again with the author field blank, because the
 * author spelling is the least reliable part of the tuple (initial order,
 * accents, consortium names) while journal/year/volume/page are exact.
 */
export async function citationMatch(ref, _email, opts = {}) {
  if (!(ref.journal && ref.year && ref.volume && ref.first_page)) return null;
  const authorVariants = [''];
  if (ref.lead_author) authorVariants.unshift(ref.lead_author);
  for (const author of authorVariants) {
    const bdata = [ref.journal, String(ref.year), ref.volume, ref.first_page,
      author, `z${ref.n}`].join('|') + '|';
    const text = await get('pubmed', `${EUTILS}/ecitmatch.cgi`, {
      params: { db: 'pubmed', retmode: 'xml', bdata },
      expectJson: false,
      ...opts,
    });
    if (!text) continue;
    const tail = text.trim().split('|').pop().trim();
    if (/^\d+$/.test(tail)) return tail;
  }
  return null;
}

/** ecitmatch first; fall back to a title-field esearch. */
export async function search(ref, email, opts = {}) {
  const pmids = [];
  const exact = await citationMatch(ref, email, opts);
  if (exact) pmids.push(exact);

  if (ref.title) {
    // [Title] restricts to the title field, which is far more precise than the
    // old code's untagged whole-reference term.
    let data = await get('pubmed', `${EUTILS}/esearch.fcgi`, {
      params: {
        db: 'pubmed', term: `"${ref.title}"[Title]`, retmax: 8, retmode: 'json', tool: TOOL, email,
      },
      ...opts,
    });
    let found = ((data || {}).esearchresult || {}).idlist || [];
    if (!found.length) {
      // loosen to all-fields when the exact title phrase isn't indexed
      data = await get('pubmed', `${EUTILS}/esearch.fcgi`, {
        params: {
          db: 'pubmed',
          term: `${ref.title} ${ref.lead_author || ''}`.trim(),
          retmax: 8,
          retmode: 'json',
          tool: TOOL,
          email,
        },
        ...opts,
      });
      found = ((data || {}).esearchresult || {}).idlist || [];
    }
    for (const p of found) if (!pmids.includes(p)) pmids.push(p);
  }

  const cands = await summaries(pmids.slice(0, 10), email, opts);
  for (const c of cands) {
    if (exact && c.pmid === exact) {
      // flag for the scorer: this one came from the deterministic matcher
      c.provider = 'pubmed:ecitmatch';
      c.providers = new Set(['pubmed:ecitmatch', 'pubmed']);
    }
  }
  return cands;
}

export async function byPmid(pmid, email, opts = {}) {
  const cands = await summaries([pmid], email, opts);
  return cands.length ? cands[0] : null;
}

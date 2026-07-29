/**
 * Port of zotprep/search/europepmc.py.
 *
 * Free, unmetered, no API key, and it returns exactly the fields the scorer
 * wants: `journalInfo.volume`, `journalInfo.issue`, `pageInfo`, plus
 * `journalInfo.journal.medlineAbbreviation` — the same NLM abbreviation style
 * Vancouver references already use, so journal matching becomes near-exact
 * rather than fuzzy.
 */
import { Candidate } from '../models.js';
import { get, queryTerms } from './base.js';
import { partition } from './crossref.js';

export const API = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search';

const TYPE_MAP = { book: 'book', bookish: 'book', preprint: 'preprint' };

/**
 * Strip Europe PMC query operators out of free text.
 *
 * Its query language treats : " ( ) [ ] { } ~ ^ ? * / and the bare words
 * AND/OR/NOT as syntax. A title containing any of them silently returns zero
 * hits rather than an error, which is how this provider quietly fails if you
 * hand it a raw title.
 *
 * Python's `\w` is Unicode-aware here, so `[^\w\s-]` keeps accented letters;
 * the ASCII JavaScript class would strip them and change the query.
 */
function sanitize(s) {
  let t = (s || '').replace(/[^\p{L}\p{N}_\s-]/gu, ' ');
  t = t.replace(/(?<![\p{L}\p{N}_])(AND|OR|NOT)(?![\p{L}\p{N}_])/gu, ' ');
  return t.replace(/\s+/g, ' ').trim();
}

function toCandidate(res) {
  const ji = res.journalInfo || {};
  const jr = ji.journal || {};
  const [fp, , lp] = partition(res.pageInfo || '', '-');
  const authors = [];
  let corporate = null;
  for (const a of ((res.authorList || {}).author || []).slice(0, 60)) {
    if (a.collectiveName) corporate = corporate || a.collectiveName;
    else if (a.lastName) authors.push(a.lastName.toLowerCase());
  }
  const year = res.pubYear;
  return new Candidate({
    provider: 'europepmc',
    title: (res.title || '').replace(/\.+$/, ''),
    doi: res.doi,
    pmid: res.pmid,
    authors,
    corporate,
    year: /^\d+$/.test(String(year)) ? parseInt(year, 10) : null,
    journal: jr.title || '',
    journal_abbrev: jr.medlineAbbreviation || jr.isoabbreviation || '',
    volume: ji.volume || null,
    issue: ji.issue || null,
    first_page: fp.trim() || null,
    last_page: lp.trim() || null,
    item_type: TYPE_MAP[(res.bookOrReportDetails && 'book') || ''] || 'journalArticle',
    raw: res,
  });
}

async function query(q, size = 8, opts = {}) {
  const data = await get('europepmc', API, {
    params: {
      query: q, format: 'json', resultType: 'core', pageSize: size,
    },
    ...opts,
  });
  const results = ((data || {}).resultList || {}).result || [];
  return results.map(toCandidate);
}

export async function search(ref, _mailto, opts = {}) {
  const [title, author] = queryTerms(ref);
  const st = sanitize(title);
  if (!st) return [];
  let out = [];

  // Pass 1: title field, phrase-quoted. High precision.
  out = out.concat(await query(`TITLE:"${st}"`, 8, opts));

  // Pass 2: only if the title phrase found nothing — avoids wasting a call.
  if (!out.length) {
    const sa = sanitize(author);
    const q2 = `"${st}"` + (sa ? ` AND AUTH:"${sa}"` : '');
    out = out.concat(await query(q2, 8, opts));
  }

  // Pass 3: the locator itself is a near-unique key when the title is odd.
  if (!out.length && ref.journal && ref.year && ref.volume && ref.first_page) {
    const q3 = `JOURNAL:"${sanitize(ref.journal)}" AND PUB_YEAR:${ref.year} `
      + `AND VOLUME:${sanitize(ref.volume)}`;
    out = out.concat(await query(q3, 25, opts));
  }
  return out;
}

export async function byDoi(doi, opts = {}) {
  const cands = await query(`DOI:"${doi}"`, 1, opts);
  return cands.length ? cands[0] : null;
}

/**
 * Port of zotprep/review.py — the confirm step for anything the accept gate
 * would not pass.
 *
 * The gate is deliberately strict, so what lands here is genuinely ambiguous —
 * a handful of references at most. The CLI asks at a prompt; the browser asks
 * with a panel of options. The decisions themselves, and what each one does to
 * the Resolution, are identical:
 *
 *   pick a candidate      -> ACCEPTED, tier "manual",         confidence 1.0
 *   paste a DOI           -> ACCEPTED, tier "manual",         confidence 1.0
 *   build from the text   -> FROM_TEXT, tier "book-from-text", confidence 0.80
 *   skip                  -> unchanged; stays flagged in the document
 *
 * One deliberate difference: the CLI reviews everything that is not ACCEPTED,
 * which includes FROM_TEXT entries. Here only REVIEW entries are offered, so a
 * run asks about the references that resolved to nothing and nothing else.
 *
 * Decisions live in memory for the session. The CLI writes each one to its
 * corrections table so the same reference never asks twice; this build does not
 * persist anything but credentials, so closing the tab forgets them.
 */
import { Candidate } from './models.js';
import { itemFromText } from './resolver.js';

/** References this build will ask about. */
export function pending(results) {
  return [...results.keys()].sort((a, b) => a - b)
    .filter((n) => results.get(n).status === 'REVIEW');
}

/**
 * The candidates to offer, matching review.py: the current pick first if there
 * is one, then alternatives, five in total.
 */
export function optionsFor(res) {
  const options = [];
  if (res.candidate) options.push(res.candidate);
  for (const c of res.alternatives || []) {
    if (options.length >= 5) break;
    if (!options.includes(c)) options.push(c);
  }
  return options;
}

function titleCase(s) {
  return String(s).replace(/[A-Za-z]+/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());
}

/** The fields review.py prints for each candidate, kept as structured data. */
export function describe(c) {
  const who = c.corporate
    || ((c.authors || []).slice(0, 3).map(titleCase).join(', ') || '?');
  const ident = c.doi || (c.pmid ? `PMID:${c.pmid}` : 'no identifier');
  return {
    title: c.title || '(untitled)',
    who,
    where: c.journal || c.publisher || '?',
    year: c.year || '?',
    locator: `v${c.volume || '?'}(${c.issue || '?'}):${c.first_page || '?'}`,
    ident,
    itemType: c.item_type,
    providers: [...(c.providers || [])].sort(),
  };
}

/** A Candidate built from a DOI the reader supplied, plus the parsed reference. */
export function candidateFromDoi(ref, doi) {
  return new Candidate({
    provider: 'manual',
    title: ref.title,
    doi,
    authors: [...ref.authors],
    corporate: ref.corporate,
    year: ref.year,
    journal: ref.journal,
    volume: ref.volume,
    issue: ref.issue,
    first_page: ref.first_page,
    last_page: ref.last_page,
    item_type: ref.is_book ? 'book' : 'journalArticle',
  });
}

/**
 * Apply one decision to a Resolution, in place.
 *
 * @param {'candidate'|'doi'|'from-text'|'skip'} kind
 * @param {object} payload  { candidate } | { doi } | {}
 */
export function applyDecision(res, ref, kind, payload = {}) {
  if (kind === 'skip') {
    res.status = 'REVIEW';
    res.tier = 'none';
    res.candidate = null;
    res.reviewed = 'skipped';
    return res;
  }
  if (kind === 'from-text') {
    res.candidate = itemFromText(ref);
    res.status = 'FROM_TEXT';
    res.tier = 'book-from-text';
    res.confidence = 0.80;
    res.reviewed = 'built from the reference text';
    return res;
  }
  if (kind === 'doi') {
    res.candidate = candidateFromDoi(ref, payload.doi);
    res.status = 'ACCEPTED';
    res.tier = 'manual';
    res.confidence = 1.0;
    res.reviewed = `accepted ${payload.doi}`;
    return res;
  }
  // a candidate the reader chose
  res.candidate = payload.candidate;
  res.status = 'ACCEPTED';
  res.tier = 'manual';
  res.confidence = 1.0;
  res.reviewed = `accepted ${payload.candidate.doi || payload.candidate.title.slice(0, 50)}`;
  return res;
}

/**
 * A DOI as the reader is likely to paste it: bare, prefixed, or as a URL.
 * Returns the normalised DOI, or null if it does not look like one at all.
 */
export function cleanDoi(input) {
  let d = String(input || '').trim();
  d = d.replace(/^https?:\/\/(dx\.)?doi\.org\//i, '');
  d = d.replace(/^doi:\s*/i, '');
  d = d.replace(/[.,;]+$/, '');
  return /^10\.\d{4,9}\/\S+$/.test(d) ? d : null;
}

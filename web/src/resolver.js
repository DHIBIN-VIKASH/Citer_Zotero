/**
 * Port of zotprep/resolver.py — one ParsedRef in, one Resolution out.
 *
 * Order of operations is deliberate and cheapest-first:
 *
 *   1. correction store  — a human already decided this one
 *   2. identifier in ref — DOI/PMID/PMCID present, so no searching at all
 *   3. resolution cache  — same reference seen earlier in this session
 *   4. provider fan-out  — five providers concurrently, title-based queries
 *   5. accept gate       — hard conjunctions, strongest tier wins
 *   6. book-from-text    — a Vancouver book reference already contains
 *                          everything Zotero needs, so build the item from the
 *                          manuscript
 *
 * The cache here is in-memory and lives for one page load only. The CLI backs
 * steps 1 and 3 with SQLite; the web build deliberately persists nothing except
 * the Zotero credentials, so a reload starts clean. Within a run the cache still
 * earns its keep — a bibliography that cites the same paper twice resolves it
 * once.
 */
import { Candidate, Resolution } from './models.js';
import { ECITMATCH_TITLE_GUARD, accept, rank, signals } from './scorer.js';
import { dedupe } from './search/base.js';
import * as crossref from './search/crossref.js';
import * as europepmc from './search/europepmc.js';
import * as openalex from './search/openalex.js';
import * as pubmed from './search/pubmed.js';
import * as semanticscholar from './search/semanticscholar.js';
import { normDoi, normText } from './utils.js';

/**
 * Build a Zotero item straight from the reference text.
 *
 * Not a guess: a complete Vancouver reference already contains every field
 * Zotero needs. For books this is the *authoritative* path, since no API adds
 * anything. For journal articles it is the honest fallback when no index holds
 * the paper — some pre-1995 economics and humanities content simply has no DOI
 * anywhere.
 *
 * Marked with its own provider name so the report never implies external
 * corroboration.
 */
export function itemFromText(ref) {
  return new Candidate({
    provider: 'reference-text',
    title: ref.title,
    authors: [...ref.authors],
    corporate: ref.corporate,
    year: ref.year,
    journal: ref.journal,
    journal_abbrev: ref.journal,
    volume: ref.volume,
    issue: ref.issue,
    first_page: ref.first_page,
    last_page: ref.last_page,
    item_type: ref.is_book ? (ref.is_chapter ? 'bookSection' : 'book') : 'journalArticle',
    book_title: ref.book_title,
    publisher: ref.publisher,
    place: ref.place,
    raw: { edition: ref.edition, source: 'parsed from reference text' },
  });
}

/**
 * Guard against fabricating an item from a reference we failed to parse.
 *
 * A book needs title + publisher; an article needs title + journal + year and at
 * least a volume or a first page. Anything less goes to human review rather than
 * into the library.
 */
export function completeEnoughForTextItem(ref) {
  if (!ref.title || ref.title.length < 8) return false;
  if (ref.is_chapter) return Boolean(ref.book_title && ref.year);
  if (ref.is_book) return Boolean(ref.publisher && ref.year);
  return Boolean(ref.journal && ref.year && (ref.volume || ref.first_page));
}

/** Token-count ratio of two titles, 0..1. */
function lengthRatio(a, b) {
  const ta = normText(a).split(' ').filter(Boolean);
  const tb = normText(b).split(' ').filter(Boolean);
  if (!ta.length || !tb.length) return 0.0;
  return Math.min(ta.length, tb.length) / Math.max(ta.length, tb.length);
}

/**
 * Loose surname agreement, to avoid crying fabrication over name variants.
 *
 * Bibliographies abbreviate compound surnames ("Mendez" for "Mendez-Guerra") and
 * providers vary on hyphenation and particles, so exact set membership is too
 * strict a basis for an accusatory advisory.
 */
function authorRelated(ref, cand) {
  const refNames = (ref.authors || []).filter(Boolean).map((a) => a.toLowerCase());
  const candNames = (cand.authors || []).filter(Boolean).map((a) => a.toLowerCase());
  for (const r of refNames) {
    for (const c of candNames) {
      if (r.startsWith(c) || c.startsWith(r)) return true;
    }
  }
  return false;
}

/** Python's str.title() on a lowercase surname. */
function titleCase(s) {
  return String(s).replace(/[A-Za-z]+/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());
}

/**
 * Advisories about a reference whose own fields disagree with each other.
 *
 * Two distinct problems both show up as a citation-matcher hit whose title does
 * not match, and they need different wording: the locator resolving to a real
 * *related* paper (usually a sibling in the same journal issue, meaning the
 * volume/pages were copied from a neighbouring reference), versus resolving to
 * something unrelated (more often the journal name itself is wrong, or the
 * "page" is an article number the matcher indexes differently).
 *
 * These are advisories, never blockers: the title is what the author meant, and
 * the correct paper is usually found by title anyway.
 */
export function locatorNotes(ref, ranked) {
  const notes = [];

  // Strongest signal of a bad reference: some real paper carries this exact
  // title, but with different authors in a different journal. A genuine
  // miscitation usually gets the authors right and fumbles the volume; a title
  // attached to the wrong authors *and* the wrong journal is characteristic of a
  // fabricated entry, and the named authors are typically real researchers in
  // the field, which is what makes it invisible on a read-through.
  for (const [cand, sg] of ranked) {
    // Comparable title lengths are required *here* — this is the one place a
    // length check is safe. A short reference title ("Economic Growth") is a
    // subset of countless longer papers, so without it the advisory accuses
    // every book of being fabricated.
    if (sg.title_sim >= 0.95
        && lengthRatio(ref.title, cand.title) >= 0.70
        && !sg.author_ok
        && sg.author_overlap === 0.0
        && !authorRelated(ref, cand)
        && !sg.journal_ok
        && cand.doi) {
      const names = cand.authors.slice(0, 3).map(titleCase).join(', ') || '?';
      notes.push(
        `VERIFY THIS ENTRY: the title matches a real paper `
        + `(${cand.doi}, ${cand.journal || '?'} ${cand.year || '?'}) whose authors `
        + `(${names}) and journal `
        + `differ entirely from this reference (${ref.lead_author || ref.corporate}, `
        + `${ref.journal}). Confirm the reference exists as cited.`,
      );
      break;
    }
  }

  for (const [, sg] of ranked) {
    if (!(sg.ecitmatch && sg.title_sim < ECITMATCH_TITLE_GUARD)) continue;
    if (sg.title_sim >= 0.60) {
      notes.push(
        `reference's journal/volume/pages (${ref.journal} ${ref.year};`
        + `${ref.volume}:${ref.first_page}) point to a DIFFERENT paper than its `
        + 'title — the volume/pages were probably copied from a neighbouring '
        + 'reference. Verify this entry.',
      );
    } else {
      notes.push(
        `nothing at ${ref.journal} ${ref.year};${ref.volume}:${ref.first_page} `
        + 'matches this title — check the journal name and volume/page numbers.',
      );
    }
    break;
  }
  return notes;
}

async function gatherCandidates(ref, mailto, ctx) {
  // Promise.allSettled is the direct equivalent of asyncio.gather with
  // return_exceptions=True: one provider throwing must never lose the others.
  const results = await Promise.allSettled([
    crossref.search(ref, mailto, ctx.opts),
    europepmc.search(ref, mailto, ctx.opts),
    pubmed.search(ref, mailto, ctx.opts),
    openalex.search(ref, mailto, ctx.opts),
    semanticscholar.search(ref, mailto, ctx.opts, ctx.s2Key),
  ]);
  let cands = [];
  for (const r of results) {
    if (r.status === 'fulfilled') cands = cands.concat(r.value);
  }
  return dedupe(cands, { normDoi, normText });
}

/**
 * Deterministic path. Crossref is preferred for DOIs because its record is the
 * most complete for Zotero's fields; OpenAlex covers PMID/PMCID lookups.
 */
async function resolveIdentifier(ref, mailto, ctx) {
  if (ref.doi) {
    let c = await crossref.byDoi(ref.doi, mailto, ctx.opts);
    if (c) return c;
    c = await openalex.byIdentifier(mailto, { doi: ref.doi }, ctx.opts);
    if (c) return c;
  }
  if (ref.pmid) {
    let c = await pubmed.byPmid(ref.pmid, mailto, ctx.opts);
    if (c) return c;
    c = await openalex.byIdentifier(mailto, { pmid: ref.pmid }, ctx.opts);
    if (c) return c;
  }
  return null;
}

export async function resolveOne(ref, mailto, cache, ctx) {
  const key = normText(ref.raw);

  // 1. a human already told us the answer
  const fixed = cache.corrections.get(key);
  if (fixed) {
    return new Resolution({
      n: ref.n, status: 'ACCEPTED', confidence: 1.0, tier: 'correction', candidate: fixed,
    });
  }

  // 2. the reference carries an identifier
  if (ref.has_identifier) {
    const c = await resolveIdentifier(ref, mailto, ctx);
    if (c) {
      return new Resolution({
        n: ref.n, status: 'ACCEPTED', confidence: 1.0, tier: 'identifier', candidate: c,
      });
    }
  }

  // 3. already resolved earlier in this session
  const hit = cache.resolutions.get(key);
  if (hit && hit.candidate) {
    return new Resolution({ ...hit, n: ref.n, reason: 'from cache' });
  }

  // 4. search
  const cands = await gatherCandidates(ref, mailto, ctx);
  const ranked = rank(ref, cands);

  // 5. accept gate, strongest tier first across all candidates
  let best = null;
  for (const [cand, sg] of ranked) {
    const [ok, tier, conf] = accept(ref, cand, sg);
    if (ok && (best === null || conf > best[0])) best = [conf, tier, cand];
  }
  if (best) {
    const [conf, tier, cand] = best;
    const res = new Resolution({
      n: ref.n,
      status: 'ACCEPTED',
      confidence: conf,
      tier,
      candidate: cand,
      alternatives: ranked.slice(0, 4).map(([c]) => c).filter((c) => c !== cand),
      signals: { ...signals(ref, cand) },
      // No locator advisory here: the reference was corroborated by a real tier,
      // so a disagreeing citation-matcher hit is just ecitmatch noise (common
      // for journals that number articles rather than pages).
    });
    cache.resolutions.set(key, res);
    return res;
  }

  // 6. nothing external holds this paper, but the reference is fully parsed
  if (completeEnoughForTextItem(ref)) {
    const kind = ref.is_book ? 'book' : 'article';
    return new Resolution({
      n: ref.n,
      status: 'FROM_TEXT',
      confidence: 0.80,
      tier: 'from-reference-text',
      candidate: itemFromText(ref),
      alternatives: ranked.slice(0, 3).map(([c]) => c),
      reason: `no index holds this ${kind}; item built from the reference text `
        + '(no external corroboration)',
      notes: locatorNotes(ref, ranked),
    });
  }

  let reason = 'no candidates returned by any provider';
  if (ranked.length) {
    reason = 'best candidate rejected: ' + ranked[0][1].failing().join('; ');
  }
  return new Resolution({
    n: ref.n,
    status: 'REVIEW',
    confidence: ranked.length ? ranked[0][2] : 0.0,
    tier: 'none',
    candidate: null,
    alternatives: ranked.slice(0, 5).map(([c]) => c),
    reason,
  });
}

export function newCache() {
  return { resolutions: new Map(), corrections: new Map() };
}

/**
 * Resolve every reference, at most `workers` in flight.
 *
 * The per-provider caps in search/base.js are what enforce politeness; this gate
 * only bounds how much work is queued at once.
 */
export async function resolveAll(refs, mailto, cache, {
  workers = 12, onProgress = null, signal = null, s2Key = '',
} = {}) {
  const ctx = { opts: signal ? { signal } : {}, s2Key };
  const ns = [...refs.keys()].sort((a, b) => a - b);
  const out = new Map();
  let cursor = 0;

  async function worker() {
    for (;;) {
      if (signal?.aborted) return;
      const i = cursor++;
      if (i >= ns.length) return;
      const ref = refs.get(ns[i]);
      try {
        out.set(ref.n, await resolveOne(ref, mailto, cache, ctx));
      } catch (err) {
        if (signal?.aborted) return;
        out.set(ref.n, new Resolution({
          n: ref.n,
          status: 'REVIEW',
          confidence: 0.0,
          tier: 'none',
          reason: `resolution failed: ${err?.message || err}`,
        }));
      }
      if (onProgress) onProgress(out.get(ref.n), out.size, ns.length);
    }
  }

  await Promise.all(Array.from({ length: Math.min(workers, ns.length) }, worker));
  return out;
}

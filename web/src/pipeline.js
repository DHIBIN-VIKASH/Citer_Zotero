/**
 * Port of the `run()` body in zotprep/cli.py — the whole job, start to finish.
 *
 * Split into two halves so the review step can sit between them, which is where
 * the CLI puts it too (`--review` runs after resolve_all and before the accept
 * list is computed):
 *
 *     resolve()  document -> parsed references -> Resolutions
 *     [review]   the reader decides the ambiguous ones
 *     finish()   enrich -> Zotero items -> rewritten .docx + report
 *
 * finish() is idempotent: it re-loads the document from the original bytes each
 * time rather than reusing the one resolve() read. That matters because
 * markBody() and removeFrom() mutate the document in place, so running finish()
 * a second time on the same object would mark up already-marked text and delete
 * from an already-truncated body. Re-reading costs a few milliseconds and makes
 * "review, then rebuild" safe to repeat as many times as the reader likes.
 *
 * The one safety rule from the CLI is carried over exactly: writing to someone's
 * Zotero library is the only irreversible thing this tool does, so it must be
 * asked for explicitly. Dry run is the default here too.
 */
import { Document, findBiblioIndex, makeRenderer, markBody, parseBibliography, removeFrom } from './docx.js';
import { parseReference } from './extractor.js';
import { newCache, resolveAll } from './resolver.js';
import { resetProviders, setLogSink } from './search/base.js';
import { ZoteroWriter, enrich, toItem } from './zotero.js';

function csvCell(v) {
  const s = v === null || v === undefined ? '' : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function toCsv(rows) {
  // Excel opens UTF-8 CSV as the system codepage unless it sees a BOM, which
  // turns every accented author name into mojibake in the report.
  return '﻿' + rows.map((r) => r.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

/**
 * Read the document, parse the bibliography, resolve every reference.
 *
 * @returns state to hand to finish(), plus `refs` and `results` for review.
 */
export async function resolve(file, opts, cb = {}) {
  const {
    mailto = 'you@example.com', live = false, userid = '', apiKey = '',
    bibliographyText = '', workers = 12, s2Key = '', signal = null,
  } = opts;
  const onStage = cb.onStage || (() => {});
  const onProgress = cb.onProgress || (() => {});
  const onLog = cb.onLog || (() => {});

  if (live && !(userid && apiKey)) {
    throw new Error('Creating items in Zotero needs a userID and an API key. '
      + 'Get both at zotero.org/settings/keys — the key needs write access.');
  }

  resetProviders();
  setLogSink(onLog);

  // Fail on bad credentials before doing several minutes of resolution work.
  if (live) {
    onStage('verifying Zotero credentials');
    await new ZoteroWriter(userid, apiKey, { dryRun: false, onLog }).verify();
  }

  onStage('reading the document');
  const buffer = await file.arrayBuffer();
  const doc = await Document.load(buffer);

  let biblioIdx;
  let biblioText;
  if (bibliographyText.trim()) {
    biblioText = bibliographyText;
    biblioIdx = doc.paragraphs.length;
  } else {
    biblioIdx = findBiblioIndex(doc);
    if (biblioIdx === null) {
      throw new Error('No "References" heading found in the document. '
        + 'Paste the bibliography into the box below, or add a References heading.');
    }
    biblioText = doc.paragraphs.slice(biblioIdx + 1).map((p) => p.text).join('\n');
  }

  const rawRefs = parseBibliography(biblioText);
  if (!rawRefs.size) {
    throw new Error('Found the bibliography but could not read any entries from it.');
  }

  const refs = new Map();
  for (const [n, t] of rawRefs) refs.set(n, parseReference(n, t));
  onLog('info', `Parsed ${refs.size} references.`);

  onStage(`resolving ${refs.size} references`);
  const cache = newCache();
  const results = await resolveAll(refs, mailto, cache, {
    workers, signal, s2Key, onProgress: (_r, done, total) => onProgress(done, total),
  });
  if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');

  return { buffer, refs, results, usedPastedBibliography: Boolean(bibliographyText.trim()) };
}

/**
 * Turn Resolutions into a rewritten .docx and a report. Safe to call repeatedly
 * on the same state — see the note at the top of this file.
 */
export async function finish(state, opts, cb = {}) {
  const {
    mailto = 'you@example.com', live = false, userid = '', apiKey = '', signal = null,
  } = opts;
  const onStage = cb.onStage || (() => {});
  const onProgress = cb.onProgress || (() => {});
  const onLog = cb.onLog || (() => {});
  const { buffer, refs, results, usedPastedBibliography } = state;
  const dryRun = !live;

  setLogSink(onLog);

  const ordered = [...results.keys()].sort((a, b) => a - b);
  const accepted = ordered.filter((n) => ['ACCEPTED', 'FROM_TEXT'].includes(results.get(n).status));
  const unresolved = ordered.filter((n) => !accepted.includes(n));

  // upgrade winning candidates to canonical Crossref records for item quality
  onStage('fetching canonical metadata');
  let done = 0;
  for (const n of accepted) {
    const res = results.get(n);
    if (res.candidate) res.candidate = await enrich(res.candidate, mailto, signal ? { signal } : {});
    onProgress(++done, accepted.length);
  }

  const writer = new ZoteroWriter(userid || '0', apiKey || '', { dryRun, onLog });
  if (!dryRun) {
    onStage('indexing your Zotero library for duplicates');
    await writer.loadLibrary((n) => onLog('info', `  indexed ${n} items`));
  }

  onStage(dryRun ? 'preparing items (dry run)' : 'creating items in Zotero');
  const items = accepted
    .filter((n) => results.get(n).candidate)
    .map((n) => [n, toItem(results.get(n).candidate, refs.get(n))]);
  const keys = await writer.create(items, (a, b) => onProgress(a, b));
  for (const [n, k] of keys) results.get(n).zotero_key = k;
  onLog('info', `Zotero items ready: ${keys.size} (${dryRun ? 'dry run' : 'created/reused'})`);

  // Re-read the document so this stage can run again after a review pass.
  onStage('rewriting citations');
  const doc = await Document.load(buffer);
  const biblioIdx = usedPastedBibliography ? doc.paragraphs.length : findBiblioIndex(doc);
  const warnings = [];
  const render = makeRenderer(results, keys, userid || '0', { refs, warnings });
  const nMarked = markBody(doc, biblioIdx, render);
  if (!usedPastedBibliography) removeFrom(doc, biblioIdx);
  const docxBlob = await doc.save();

  const rows = [[
    'n', 'status', 'tier', 'confidence', 'doi', 'pmid', 'zotero_key',
    'resolved_title', 'reason', 'advisory', 'raw_reference',
  ]];
  for (const n of ordered) {
    const r = results.get(n);
    const c = r.candidate;
    rows.push([n, r.status, r.tier, r.confidence, c ? c.doi : '', c ? c.pmid : '',
      r.zotero_key || '', c ? c.title : '', r.reason, (r.notes || []).join(' | '),
      refs.get(n).raw]);
  }

  const advisories = [];
  for (const n of ordered) {
    for (const note of results.get(n).notes || []) advisories.push([n, note]);
  }

  return {
    docxBlob,
    reportCsv: new Blob([toCsv(rows)], { type: 'text/csv;charset=utf-8' }),
    results,
    refs,
    keys,
    stats: {
      total: refs.size,
      accepted: accepted.length,
      unresolved,
      nMarked,
      dryRun,
      created: keys.size,
    },
    warnings: [...new Set(warnings)],
    advisories,
  };
}

/** resolve() then finish(), for callers that do not review in between. */
export async function run(file, opts, cb = {}) {
  const state = await resolve(file, opts, cb);
  const out = await finish(state, opts, cb);
  return { ...out, state };
}

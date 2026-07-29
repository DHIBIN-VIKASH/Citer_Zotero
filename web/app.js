/**
 * UI wiring for the browser build.
 *
 * Credentials are the only thing persisted, in localStorage — the browser
 * equivalent of the CLI's ~/.zotprep/config.json, and for the same reason: not
 * re-entering a userID and API key on every run. The manuscript, the resolved
 * results and the resolution cache all live in memory and go away on reload.
 */
import { finish, resolve } from './src/pipeline.js';
import { applyDecision, cleanDoi, describe, optionsFor, pending } from './src/review.js';

const $ = (id) => document.getElementById(id);
const STORE = 'zotprep.credentials.v1';

const els = {
  tray: $('tray'), file: $('file'), trayTitle: $('trayTitle'), trayHint: $('trayHint'),
  uid: $('uid'), key: $('key'), mailto: $('mailto'), live: $('live'),
  biblio: $('biblio'), s2: $('s2'), workers: $('workers'),
  go: $('go'), cancel: $('cancel'), mode: $('mode'), credHint: $('credHint'),
  err: $('err'), errMsg: $('errMsg'),
  progress: $('progress'), stage: $('stage'), count: $('count'), bar: $('bar'),
  results: $('results'), downloads: $('downloads'), rows: $('rows'),
  tTotal: $('tTotal'), tOk: $('tOk'), tRev: $('tRev'), tMark: $('tMark'),
  advice: $('advice'), adviceWrap: $('adviceWrap'),
  warns: $('warns'), warnWrap: $('warnWrap'),
  log: $('log'), logHead: $('logHead'), settings: $('settings'),
  review: $('review'), cards: $('cards'), apply: $('apply'), skipAll: $('skipAll'),
  revCount: $('revCount'), revLede: $('revLede'),
};

let chosenFile = null;
let controller = null;
let objectUrls = [];
// state from resolve(), kept so finish() can be re-run after a review pass
let runState = null;
let runOpts = null;
// n -> { kind, payload } for references the reader has decided this session
let decisions = new Map();

// ── persisted credentials ────────────────────────────────────────────────────
function loadCreds() {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(STORE) || '{}');
  } catch {
    saved = {};
  }
  els.uid.value = saved.userid || '';
  els.key.value = saved.apiKey || '';
  els.mailto.value = saved.mailto || '';
  els.s2.value = saved.s2Key || '';
  refreshCredHint();
}

function saveCreds() {
  const payload = {
    userid: els.uid.value.trim(),
    apiKey: els.key.value.trim(),
    mailto: els.mailto.value.trim(),
    s2Key: els.s2.value.trim(),
  };
  try {
    if (Object.values(payload).some(Boolean)) localStorage.setItem(STORE, JSON.stringify(payload));
    else localStorage.removeItem(STORE);
  } catch {
    log('warn', 'This browser refused to save the credentials (private mode?), '
      + 'so they will need re-entering next time.');
  }
  refreshCredHint();
}

function refreshCredHint() {
  const hasKey = Boolean(els.uid.value.trim() && els.key.value.trim());
  els.credHint.textContent = hasKey ? 'saved in this browser' : 'not set';
  els.credHint.style.color = hasKey ? 'var(--sage)' : '';
}

for (const el of [els.uid, els.key, els.mailto, els.s2]) {
  el.addEventListener('change', saveCreds);
  el.addEventListener('blur', saveCreds);
}

// ── mode indicator ───────────────────────────────────────────────────────────
function refreshMode() {
  if (els.live.checked) {
    els.mode.innerHTML = 'mode <b class="hot">live</b> — items will be created in Zotero';
    if (!(els.uid.value.trim() && els.key.value.trim())) els.settings.open = true;
  } else {
    els.mode.innerHTML = 'mode <b>dry run</b> — nothing will be written';
  }
}
els.live.addEventListener('change', refreshMode);

// ── file selection ───────────────────────────────────────────────────────────
function setFile(f) {
  if (!f) return;
  if (!f.name.toLowerCase().endsWith('.docx')) {
    showError('That is not a .docx. Word\'s older .doc format and PDFs cannot be read — '
      + 'open the file in Word and "Save As" .docx first.');
    return;
  }
  chosenFile = f;
  els.tray.classList.add('loaded');
  els.trayTitle.innerHTML = `<span class="filename">${escapeHtml(f.name)}</span>`;
  els.trayHint.innerHTML = `<span class="filesize">${(f.size / 1024).toFixed(0)} KB — `
    + 'click to choose a different file</span>';
  els.go.disabled = false;
  hideError();
}

els.tray.addEventListener('click', () => els.file.click());
els.tray.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); els.file.click(); }
});
els.file.addEventListener('change', () => setFile(els.file.files[0]));

for (const ev of ['dragenter', 'dragover']) {
  els.tray.addEventListener(ev, (e) => { e.preventDefault(); els.tray.classList.add('over'); });
}
for (const ev of ['dragleave', 'drop']) {
  els.tray.addEventListener(ev, (e) => { e.preventDefault(); els.tray.classList.remove('over'); });
}
els.tray.addEventListener('drop', (e) => {
  const f = e.dataTransfer?.files?.[0];
  if (f) setFile(f);
});
// dropping anywhere else must not navigate away from a half-finished run
for (const ev of ['dragover', 'drop']) {
  window.addEventListener(ev, (e) => { if (e.target !== els.tray) e.preventDefault(); });
}

// ── helpers ──────────────────────────────────────────────────────────────────
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function log(level, msg) {
  els.logHead.hidden = false;
  const line = document.createElement('div');
  line.className = level;
  line.textContent = (level === 'warn' ? '! ' : '  ') + msg;
  els.log.appendChild(line);
  els.log.scrollTop = els.log.scrollHeight;
}

function showError(msg) {
  els.errMsg.textContent = msg;
  els.err.classList.add('on');
  els.err.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
function hideError() { els.err.classList.remove('on'); }

function setStage(text, { determinate = false } = {}) {
  els.progress.classList.add('on');
  els.stage.textContent = text;
  els.count.textContent = '';
  els.bar.classList.toggle('indet', !determinate);
  if (!determinate) els.bar.querySelector('i').style.width = '';
}

function setProgress(done, total) {
  els.bar.classList.remove('indet');
  els.count.textContent = `${done} / ${total}`;
  els.bar.querySelector('i').style.width = `${total ? (done / total) * 100 : 0}%`;
}

const BADGE = {
  ACCEPTED: ['b-acc', 'resolved'],
  FROM_TEXT: ['b-txt', 'from text'],
  REVIEW: ['b-rev', 'review'],
};

function renderResults(out) {
  const { stats, results, refs } = out;
  els.tTotal.textContent = stats.total;
  els.tOk.textContent = stats.accepted;
  els.tRev.textContent = stats.unresolved.length;
  els.tMark.textContent = stats.nMarked;

  // downloads
  for (const u of objectUrls) URL.revokeObjectURL(u);
  objectUrls = [];
  const base = (chosenFile.name || 'manuscript').replace(/\.docx$/i, '');
  els.downloads.innerHTML = '';
  for (const [blob, name, title, note] of [
    [out.docxBlob, `${base}_scannable.docx`, 'Scannable manuscript',
      'open Zotero → Tools → ODF Scan'],
    [out.reportCsv, `${base}_report.csv`, 'Resolution report',
      'one row per reference'],
  ]) {
    const url = URL.createObjectURL(blob);
    objectUrls.push(url);
    const a = document.createElement('a');
    a.className = 'dl';
    a.href = url;
    a.download = name;
    a.innerHTML = `<span class="ic">↓</span><span><span class="t">${title}</span><br>`
      + `<span class="s">${note}</span></span>`;
    els.downloads.appendChild(a);
  }

  // advisories and warnings
  els.adviceWrap.hidden = !out.advisories.length;
  els.advice.innerHTML = out.advisories
    .map(([n, note]) => `<p><span class="num">[${n}]</span> ${escapeHtml(note)}</p>`).join('');
  els.warnWrap.hidden = !out.warnings.length;
  els.warns.innerHTML = out.warnings.map((w) => `<p>${escapeHtml(w)}</p>`).join('');

  // table
  const frag = document.createDocumentFragment();
  for (const n of [...results.keys()].sort((a, b) => a - b)) {
    const r = results.get(n);
    const c = r.candidate;
    const [cls, label] = BADGE[r.status] || ['b-rev', r.status.toLowerCase()];
    const tr = document.createElement('tr');
    const ident = c ? (c.doi || (c.pmid ? `PMID ${c.pmid}` : '')) : '';
    const shown = c ? c.title : (refs.get(n).title || refs.get(n).raw);
    tr.innerHTML = `
      <td class="n">${n}</td>
      <td><span class="badge ${cls}">${label}</span></td>
      <td class="ttl">${escapeHtml(shown || '—')}${
  r.reason ? `<span class="why">${escapeHtml(r.reason)}</span>` : ''}</td>
      <td class="tier">${escapeHtml(r.tier || '')}</td>
      <td class="doi">${escapeHtml(ident)}</td>`;
    frag.appendChild(tr);
  }
  els.rows.replaceChildren(frag);
  els.results.classList.add('on');
}

// ── review ───────────────────────────────────────────────────────────────────
/**
 * One card per unresolved reference. Mirrors review.py's prompt: the candidates
 * it would list, in the order it lists them, and the same four outcomes.
 */
function renderReview(out) {
  const ns = pending(out.results);
  els.review.classList.toggle('on', ns.length > 0);
  if (!ns.length) {
    els.cards.replaceChildren();
    return;
  }

  const undecided = ns.filter((n) => !decisions.has(n)).length;
  els.revLede.textContent = ns.length === 1
    ? 'One reference resolved to nothing.'
    : `${ns.length} references resolved to nothing.`;

  const frag = document.createDocumentFragment();
  for (const n of ns) {
    const res = out.results.get(n);
    const ref = out.refs.get(n);
    const card = document.createElement('div');
    card.className = 'card' + (decisions.has(n) ? ' done' : '');

    const head = document.createElement('header');
    head.innerHTML = `<span class="idx">${n}</span><span class="raw">${escapeHtml(ref.raw)}`
      + `${res.reason ? `<span class="why">${escapeHtml(res.reason)}</span>` : ''}</span>`;
    card.appendChild(head);

    const body = document.createElement('div');
    body.className = 'card-body';

    if (decisions.has(n)) {
      const d = decisions.get(n);
      const v = document.createElement('div');
      v.className = 'verdict' + (d.kind === 'skip' ? ' skip' : '');
      v.innerHTML = `<span>${escapeHtml(d.label)}</span>`;
      const change = document.createElement('button');
      change.className = 'mini';
      change.textContent = 'change';
      change.addEventListener('click', () => { decisions.delete(n); renderReview(out); });
      v.appendChild(change);
      body.appendChild(v);
      card.appendChild(body);
      frag.appendChild(card);
      continue;
    }

    const options = optionsFor(res);
    if (!options.length) {
      const p = document.createElement('p');
      p.className = 'none';
      p.textContent = 'No provider returned a candidate for this reference. '
        + 'Paste a DOI if you have one, or leave it flagged.';
      body.appendChild(p);
    }

    options.forEach((c, i) => {
      const d = describe(c);
      const opt = document.createElement('label');
      opt.className = 'opt';
      opt.innerHTML = `
        <div class="ot">${escapeHtml(d.title)}${
  c === res.candidate ? '<span class="cur">current pick</span>' : ''}</div>
        <div class="om">
          <span><b>${escapeHtml(d.who)}</b></span>
          <span>${escapeHtml(d.where)}</span>
          <span>${escapeHtml(String(d.year))}</span>
          <span>${escapeHtml(d.locator)}</span>
          <span>${escapeHtml(d.ident)}</span>
          <span>${escapeHtml(d.itemType)}</span>
          <span class="pv">${escapeHtml(d.providers.join(' · '))}</span>
        </div>`;
      opt.addEventListener('click', () => {
        decisions.set(n, {
          kind: 'candidate',
          payload: { candidate: c },
          label: `accepted ${c.doi || c.title.slice(0, 60)}`,
        });
        renderReview(out);
      });
      opt.tabIndex = 0;
      opt.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); opt.click(); }
      });
      opt.dataset.i = String(i + 1);
      body.appendChild(opt);
    });

    // actions: skip / paste a DOI / build from the reference text (books only,
    // matching review.py, where [b] is offered only when ref.is_book)
    const actions = document.createElement('div');
    actions.className = 'rev-actions';

    const skip = document.createElement('button');
    skip.className = 'mini';
    skip.textContent = 'Leave flagged';
    skip.addEventListener('click', () => {
      decisions.set(n, { kind: 'skip', payload: {}, label: 'skipped — stays flagged in the document' });
      renderReview(out);
    });
    actions.appendChild(skip);

    const doiBtn = document.createElement('button');
    doiBtn.className = 'mini';
    doiBtn.textContent = 'Paste a DOI';
    actions.appendChild(doiBtn);

    if (ref.is_book) {
      const fromText = document.createElement('button');
      fromText.className = 'mini';
      fromText.textContent = 'Build from reference text';
      fromText.title = 'A complete Vancouver book reference already contains every field '
        + 'Zotero needs, so no index has to hold it.';
      fromText.addEventListener('click', () => {
        decisions.set(n, {
          kind: 'from-text', payload: {}, label: 'built from the reference text',
        });
        renderReview(out);
      });
      actions.appendChild(fromText);
    }

    const doiRow = document.createElement('div');
    doiRow.className = 'doi-row';
    const doiIn = document.createElement('input');
    doiIn.type = 'text';
    doiIn.placeholder = '10.1016/S0140-6736(17)32804-0';
    doiIn.spellcheck = false;
    const doiOk = document.createElement('button');
    doiOk.className = 'mini';
    doiOk.textContent = 'Use it';
    const takeDoi = () => {
      const doi = cleanDoi(doiIn.value);
      if (!doi) {
        doiIn.style.borderColor = 'var(--terra)';
        doiIn.placeholder = 'that does not look like a DOI — expected 10.xxxx/…';
        doiIn.value = '';
        return;
      }
      decisions.set(n, { kind: 'doi', payload: { doi }, label: `accepted ${doi}` });
      renderReview(out);
    };
    doiOk.addEventListener('click', takeDoi);
    doiIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') takeDoi(); });
    doiRow.append(doiIn, doiOk);

    doiBtn.addEventListener('click', () => {
      doiRow.classList.toggle('on');
      doiBtn.classList.toggle('on');
      if (doiRow.classList.contains('on')) doiIn.focus();
    });

    actions.appendChild(doiRow);
    body.appendChild(actions);
    card.appendChild(body);
    frag.appendChild(card);
  }
  els.cards.replaceChildren(frag);

  els.apply.disabled = decisions.size === 0;
  els.skipAll.hidden = undecided === 0;
  els.revCount.innerHTML = decisions.size
    ? `<b>${decisions.size}</b> decided · ${undecided} left`
    : `${ns.length} awaiting a decision`;
}

/** Apply the session's decisions and regenerate the document and report. */
async function applyAndRebuild() {
  els.apply.disabled = true;
  els.skipAll.disabled = true;
  controller = new AbortController();
  try {
    for (const [n, d] of decisions) {
      applyDecision(runState.results.get(n), runState.refs.get(n), d.kind, d.payload);
    }
    const decided = decisions.size;
    decisions = new Map();

    const out = await finish(runState, { ...runOpts, signal: controller.signal }, {
      onStage: (s) => setStage(s), onProgress: setProgress, onLog: log,
    });
    els.progress.classList.remove('on');
    renderResults(out);
    renderReview(out);
    log('info', `Applied ${decided} decision(s) and rebuilt: `
      + `${out.stats.accepted} of ${out.stats.total} resolved, `
      + `${out.stats.nMarked} in-text citations rewritten.`);
    els.results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    if (e?.name !== 'AbortError') showError(e?.message || String(e));
    els.progress.classList.remove('on');
  } finally {
    els.apply.disabled = decisions.size === 0;
    els.skipAll.disabled = false;
    controller = null;
  }
}

els.apply.addEventListener('click', applyAndRebuild);
els.skipAll.addEventListener('click', () => {
  for (const n of pending(runState.results)) {
    if (!decisions.has(n)) {
      decisions.set(n, { kind: 'skip', payload: {}, label: 'skipped — stays flagged in the document' });
    }
  }
  applyAndRebuild();
});

// ── the run ──────────────────────────────────────────────────────────────────
els.go.addEventListener('click', async () => {
  if (!chosenFile) return;
  hideError();
  els.results.classList.remove('on');
  els.log.replaceChildren();
  els.logHead.hidden = true;
  els.go.disabled = true;
  els.cancel.hidden = false;
  els.review.classList.remove('on');
  decisions = new Map();
  controller = new AbortController();
  saveCreds();

  const started = performance.now();
  try {
    runOpts = {
      mailto: els.mailto.value.trim() || 'you@example.com',
      live: els.live.checked,
      userid: els.uid.value.trim(),
      apiKey: els.key.value.trim(),
      bibliographyText: els.biblio.value,
      s2Key: els.s2.value.trim(),
      workers: Math.max(1, Math.min(32, parseInt(els.workers.value, 10) || 12)),
      signal: controller.signal,
    };
    const cbs = { onStage: (s) => setStage(s), onProgress: setProgress, onLog: log };

    runState = await resolve(chosenFile, runOpts, cbs);
    const out = await finish(runState, runOpts, cbs);

    els.progress.classList.remove('on');
    renderResults(out);
    renderReview(out);
    const secs = ((performance.now() - started) / 1000).toFixed(0);
    log('info', `Done in ${secs}s. ${out.stats.accepted} of ${out.stats.total} resolved, `
      + `${out.stats.nMarked} in-text citations rewritten`
      + `${out.stats.dryRun ? ' (dry run — nothing written to Zotero)' : ''}.`);
    if (out.stats.unresolved.length) {
      log('warn', `${out.stats.unresolved.length} unresolved: [${out.stats.unresolved.join(', ')}] `
        + '— marked "{NEEDS REVIEW: n}" in the document. '
        + 'Decide them below, then rebuild.');
    }
    els.results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    if (e?.name === 'AbortError') {
      els.progress.classList.remove('on');
      log('warn', 'Cancelled. Nothing was written.');
    } else {
      els.progress.classList.remove('on');
      showError(e?.message || String(e));
    }
  } finally {
    els.go.disabled = false;
    els.cancel.hidden = true;
    controller = null;
  }
});

els.cancel.addEventListener('click', () => controller?.abort());

window.addEventListener('beforeunload', (e) => {
  if (controller) { e.preventDefault(); e.returnValue = ''; }
});

// ── capability check ─────────────────────────────────────────────────────────
// DecompressionStream is what reads the .docx. Failing here with a clear message
// beats failing later with "undefined is not a constructor".
if (typeof DecompressionStream === 'undefined' || typeof CompressionStream === 'undefined') {
  els.go.disabled = true;
  showError('This browser cannot read .docx files — it is missing Compression Streams. '
    + 'Chrome 80+, Edge 80+, Firefox 113+ or Safari 16.4+ will work.');
}

loadCreds();
refreshMode();

/**
 * Port of zotprep/search/base.py — shared HTTP, per-host rate limiting, retries.
 *
 * The politeness limits are not decoration. Every reference hits every provider,
 * so capping the number of references in flight does not cap the request rate;
 * the caps and the minimum inter-request intervals below are what actually keep
 * NCBI and Crossref from returning 429s. They are carried over unchanged.
 *
 * Browser specifics:
 *
 *   - `fetch` replaces httpx. Every provider used here sends
 *     `Access-Control-Allow-Origin: *`, so no proxy is involved.
 *   - A response body can only be read once, so the text is taken first and
 *     parsed second — the quota-marker check needs the body of a 402/429, and
 *     the normal path needs the same body as JSON.
 *   - `User-Agent` is a forbidden header in browsers and is silently dropped, so
 *     the polite-pool identification rides on the `mailto`/`tool`/`email`
 *     parameters that each provider already accepts.
 *   - AbortSignal.timeout replaces httpx's timeout.
 */

// Politeness limits: per-provider concurrency caps.
export const RATE_LIMITS = {
  openalex: 4,
  crossref: 4,
  europepmc: 6,
  pubmed: 3, // NCBI allows 3/s without an API key
  semanticscholar: 1, // unauthenticated shared pool is ~1/s
};

// Minimum milliseconds between two requests to the same provider. Concurrency
// caps alone don't satisfy a requests-per-second limit, so pace them too.
export const MIN_INTERVAL = {
  openalex: 50,
  crossref: 60,
  europepmc: 50,
  pubmed: 350, // stay under NCBI's 3/s
  semanticscholar: 1100,
};

// Providers that have taken themselves out of the run (metered quota exhausted,
// credentials rejected). Checked before every request so one 402/429-with-budget
// response stops us hammering a provider that cannot answer today.
export const DISABLED = new Map();

// OpenAlex moved to a metered model: unauthenticated callers get a small daily
// budget and then return 429 with this text. Retrying is pointless.
const QUOTA_MARKERS = ['insufficient budget', 'add funds', 'quota exceeded', 'payment required'];

/** Called with (level, message) for anything the user should see. */
let logSink = () => {};
export function setLogSink(fn) { logSink = fn || (() => {}); }
function warn(msg) { logSink('warn', msg); }

export function disable(provider, why) {
  if (!DISABLED.has(provider)) {
    DISABLED.set(provider, why);
    warn(`${provider} disabled for this run: ${why}`);
  }
}

export function resetProviders() {
  DISABLED.clear();
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Counting semaphore, standing in for asyncio.Semaphore. */
class Semaphore {
  constructor(n) { this.free = n; this.queue = []; }

  async acquire() {
    if (this.free > 0) { this.free--; return; }
    await new Promise((resolve) => this.queue.push(resolve));
  }

  release() {
    const next = this.queue.shift();
    if (next) next();
    else this.free++;
  }
}

const semaphores = new Map();
function semaphore(provider) {
  if (!semaphores.has(provider)) {
    semaphores.set(provider, new Semaphore(RATE_LIMITS[provider] ?? 4));
  }
  return semaphores.get(provider);
}

// Serialised pacing per provider: each caller chains onto the previous one's
// promise, which is what the asyncio.Lock around _last_call achieves.
const paceChain = new Map();
function pace(provider) {
  const interval = MIN_INTERVAL[provider] ?? 50;
  const prev = paceChain.get(provider) ?? Promise.resolve(0);
  const next = prev.then(async (last) => {
    const wait = last + interval - performance.now();
    if (wait > 0) await sleep(wait);
    return performance.now();
  });
  paceChain.set(provider, next);
  return next;
}

function withParams(url, params) {
  if (!params) return url;
  const u = new URL(url);
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    u.searchParams.set(k, String(v));
  }
  return u.toString();
}

/**
 * Rate-limited GET with exponential backoff on 429/5xx.
 *
 * Returns parsed JSON (or text when expectJson is false), or null on permanent
 * failure. A dead provider must never abort the run — the whole point of the
 * multi-provider design is graceful degradation.
 */
export async function get(provider, url, {
  params = null, headers = null, attempts = 4, expectJson = true, signal = null,
} = {}) {
  if (DISABLED.has(provider)) return null;
  let delay = 1000;
  const sem = semaphore(provider);
  await sem.acquire();
  try {
    for (let attempt = 0; attempt < attempts; attempt++) {
      if (DISABLED.has(provider)) return null;
      try {
        await pace(provider);
        const res = await fetch(withParams(url, params), {
          headers: { Accept: 'application/json', ...(headers || {}) },
          signal: signal ?? AbortSignal.timeout(30000),
          redirect: 'follow',
        });
        const body = await res.text();

        if ((res.status === 402 || res.status === 429)
            && QUOTA_MARKERS.some((m) => body.toLowerCase().includes(m))) {
          // A quota wall, not congestion. Backing off cannot help, and retrying
          // 4x per call turns a fast run into a slow one.
          disable(provider, `HTTP ${res.status}: metered quota exhausted`);
          return null;
        }
        if ([429, 500, 502, 503, 504].includes(res.status)) {
          throw new Error(`retryable HTTP ${res.status}`);
        }
        if (res.status >= 400 && res.status < 500) {
          // A malformed query or a rejected field. Retrying sends the identical
          // request, so report it loudly once and move on — a silently-zeroed
          // provider is the worst failure mode here, because the run still
          // "succeeds" with fewer votes.
          if (res.status !== 404) {
            warn(`${provider} rejected request: HTTP ${res.status} ${body.slice(0, 200)}`);
          }
          return null;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        if (!expectJson) return body;
        try {
          return JSON.parse(body);
        } catch {
          warn(`${provider} returned unparseable JSON`);
          return null;
        }
      } catch (err) {
        if (err?.name === 'AbortError' && signal?.aborted) throw err; // user cancelled
        if (attempt === attempts - 1) {
          warn(`${provider} gave up: ${err?.message || err}`);
          return null;
        }
        await sleep(delay + Math.random() * 400);
        delay *= 2;
      }
    }
    return null;
  } finally {
    sem.release();
  }
}

/**
 * The free-text query pair: [title, author-ish token].
 *
 * Title-only searching is the rule, with one exception: the author token is
 * passed as a *separate* field so short generic titles ("Convergence") are still
 * discriminable. Journal, volume and page numbers stay out of the query
 * entirely — they are scoring evidence, not search terms.
 */
export function queryTerms(ref) {
  const author = ref.lead_author || (ref.corporate || '');
  return [ref.title, author];
}

function richness(c) {
  return [c.doi, c.pmid, c.volume, c.first_page, c.journal, c.year,
    c.authors && c.authors.length].filter(Boolean).length;
}

/**
 * Merge candidates that are the same paper, recording provider agreement.
 *
 * Keyed on normalized DOI when present, else normalized title+year. Cross-
 * provider agreement is a first-class scoring signal, so the merge must preserve
 * which providers voted for each paper.
 */
export function dedupe(cands, { normDoi, normText }) {
  const out = new Map();
  for (const c of cands) {
    c.doi = normDoi(c.doi);
    const key = c.doi || `${normText(c.title)}|${c.year}`;
    // Python's key.strip("|") — drop entries with neither a title nor a year
    if (!key.replace(/^\|+|\|+$/g, '')) continue;
    if (out.has(key)) {
      const existing = out.get(key);
      for (const p of (c.providers && c.providers.size ? c.providers : new Set([c.provider]))) {
        existing.providers.add(p);
      }
      // keep the richest record: prefer one that has locator metadata
      if (richness(c) > richness(existing)) {
        c.providers = existing.providers;
        out.set(key, c);
      }
    } else {
      out.set(key, c);
    }
  }
  return [...out.values()];
}

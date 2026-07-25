"""Provider plumbing: shared HTTP client, per-host rate limiting, retries.

Adding a provider means writing one module here that exposes
`async def search(client, ref) -> list[Candidate]` and registering it in
PROVIDERS. Nothing else in the codebase changes — that is how a Google/Serper
provider gets dropped in later.
"""
from __future__ import annotations

import asyncio
import random
import sys

import httpx

from ..models import Candidate, ParsedRef

# Politeness limits. These are per-provider concurrency caps, which is what
# actually prevents 429s — capping the number of *references* in flight does not,
# because every reference hits every provider.
RATE_LIMITS = {
    "openalex": 4,
    "crossref": 4,
    "europepmc": 6,
    "pubmed": 3,  # NCBI allows 3/s without an API key
    "semanticscholar": 1,  # unauthenticated shared pool is ~1/s
}

# Providers that have taken themselves out of the run (metered quota exhausted,
# credentials rejected). Checked before every request so one 402/429-with-budget
# response stops us hammering a provider that cannot answer today.
DISABLED: dict[str, str] = {}

# OpenAlex moved to a metered model: unauthenticated callers get a small daily
# budget and then return 429 with this text. Retrying is pointless.
_QUOTA_MARKERS = ("insufficient budget", "add funds", "quota exceeded", "payment required")


def disable(provider: str, why: str) -> None:
    if provider not in DISABLED:
        DISABLED[provider] = why
        print(f"    ! {provider} disabled for this run: {why}", file=sys.stderr)

# Minimum seconds between two requests to the same provider. Concurrency caps
# alone don't satisfy a requests-per-second limit, so pace the requests too.
MIN_INTERVAL = {
    "openalex": 0.05,
    "crossref": 0.06,
    "europepmc": 0.05,
    "pubmed": 0.35,  # stay under NCBI's 3/s
    "semanticscholar": 1.10,
}

_semaphores: dict[str, asyncio.Semaphore] = {}
_pace_locks: dict[str, asyncio.Lock] = {}
_last_call: dict[str, float] = {}


def semaphore(provider: str) -> asyncio.Semaphore:
    if provider not in _semaphores:
        _semaphores[provider] = asyncio.Semaphore(RATE_LIMITS.get(provider, 4))
    return _semaphores[provider]


async def _pace(provider: str) -> None:
    interval = MIN_INTERVAL.get(provider, 0.05)
    lock = _pace_locks.setdefault(provider, asyncio.Lock())
    async with lock:
        loop = asyncio.get_running_loop()
        wait = _last_call.get(provider, 0.0) + interval - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call[provider] = asyncio.get_running_loop().time()


async def get(
    client: httpx.AsyncClient,
    provider: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    attempts: int = 4,
    expect_json: bool = True,
):
    """Rate-limited GET with exponential backoff on 429/5xx.

    Returns parsed JSON (or text when expect_json is False), or None on
    permanent failure. A dead provider must never abort the run — the whole
    point of the multi-provider design is graceful degradation.
    """
    if provider in DISABLED:
        return None
    delay = 1.0
    async with semaphore(provider):
        for attempt in range(attempts):
            if provider in DISABLED:
                return None
            try:
                await _pace(provider)
                r = await client.get(url, params=params, headers=headers)
                if r.status_code in (402, 429) and any(
                    m in r.text.lower() for m in _QUOTA_MARKERS
                ):
                    # A quota wall, not congestion. Backing off cannot help, and
                    # retrying 4x per call turns a fast run into a slow one.
                    disable(provider, f"HTTP {r.status_code}: metered quota exhausted")
                    return None
                if r.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                if 400 <= r.status_code < 500:
                    # A malformed query or a rejected field. Retrying sends the
                    # identical request, so report it loudly once and move on —
                    # a silently-zeroed provider is the worst failure mode here,
                    # because the run still "succeeds" with fewer votes.
                    if r.status_code != 404:
                        print(
                            f"    ! {provider} rejected request: HTTP {r.status_code} "
                            f"{r.text[:200]!r}",
                            file=sys.stderr,
                        )
                    return None
                r.raise_for_status()
                return r.json() if expect_json else r.text
            except Exception as exc:  # noqa: BLE001 - provider errors are expected
                if attempt == attempts - 1:
                    detail = type(exc).__name__
                    resp = getattr(exc, "response", None)
                    if resp is not None:
                        detail = f"HTTP {resp.status_code} {resp.text[:180]!r}"
                    print(f"    ! {provider} gave up: {detail}", file=sys.stderr)
                    return None
                await asyncio.sleep(delay + random.random() * 0.4)
                delay *= 2
    return None


def query_terms(ref: ParsedRef) -> tuple[str, str]:
    """The free-text query pair: (title, author-ish token).

    Title-only searching is the rule, with one exception: the author token is
    passed as a *separate* field so short generic titles ("Convergence") are
    still discriminable. Journal, volume and page numbers stay out of the query
    entirely — they are scoring evidence, not search terms.
    """
    author = ref.lead_author or (ref.corporate or "")
    return ref.title, author


def dedupe(cands: list[Candidate]) -> list[Candidate]:
    """Merge candidates that are the same paper, recording provider agreement.

    Keyed on normalized DOI when present, else normalized title+year. Cross-
    provider agreement is a first-class scoring signal, so the merge must
    preserve which providers voted for each paper.
    """
    from ..utils import norm_doi, norm_text

    out: dict[str, Candidate] = {}
    for c in cands:
        c.doi = norm_doi(c.doi)
        key = c.doi or f"{norm_text(c.title)}|{c.year}"
        if not key.strip("|"):
            continue
        if key in out:
            existing = out[key]
            existing.providers |= c.providers or {c.provider}
            # keep the richest record: prefer one that has locator metadata
            if _richness(c) > _richness(existing):
                c.providers = existing.providers
                out[key] = c
        else:
            out[key] = c
    return list(out.values())


def _richness(c: Candidate) -> int:
    return sum(
        bool(x) for x in (c.doi, c.pmid, c.volume, c.first_page, c.journal, c.year, c.authors)
    )

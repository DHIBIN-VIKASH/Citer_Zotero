"""Semantic Scholar provider.

Third vote for cross-provider agreement, and decent coverage of economics and
older monographs. The unauthenticated endpoint is aggressively rate limited, so
this provider is treated as strictly optional: a 429 storm degrades it to
returning nothing, which costs a vote but never blocks a resolution.
"""
from __future__ import annotations

import os

import httpx

from ..models import Candidate, ParsedRef
from ..utils import surname_of
from .base import get, query_terms

API = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,year,venue,externalIds,authors,publicationVenue,journal,publicationTypes"

TYPE_MAP = {"Book": "book", "BookSection": "bookSection", "Conference": "conferencePaper"}


def _to_candidate(p: dict) -> Candidate:
    ext = p.get("externalIds") or {}
    journal = p.get("journal") or {}
    venue_obj = p.get("publicationVenue") or {}
    pages = journal.get("pages") or ""
    fp, _, lp = pages.partition("-")
    ptypes = p.get("publicationTypes") or []
    item_type = next((TYPE_MAP[t] for t in ptypes if t in TYPE_MAP), "journalArticle")
    return Candidate(
        provider="semanticscholar",
        title=p.get("title") or "",
        doi=ext.get("DOI"),
        pmid=str(ext["PubMed"]) if ext.get("PubMed") else None,
        authors=[surname_of(a.get("name", "")) for a in (p.get("authors") or [])[:60] if a.get("name")],
        year=p.get("year"),
        journal=journal.get("name") or venue_obj.get("name") or p.get("venue") or "",
        journal_abbrev=(venue_obj.get("alternate_names") or [""])[0] if venue_obj else "",
        volume=(journal.get("volume") or "").strip() or None,
        first_page=fp.strip() or None,
        last_page=lp.strip() or None,
        item_type=item_type,
        raw=p,
    )


async def search(client: httpx.AsyncClient, ref: ParsedRef, mailto: str) -> list[Candidate]:
    """Opt-in: set SEMANTIC_SCHOLAR_API_KEY to enable.

    Without a key this endpoint 429s on nearly every call, and its 1.1s pacing
    serializes the whole run for a provider that contributes nothing. A free key
    from semanticscholar.org/product/api removes both problems, so the provider
    stays wired up but stays off until there is one.
    """
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if not key:
        return []
    title, author = query_terms(ref)
    if not title:
        return []
    data = await get(
        client,
        "semanticscholar",
        API,
        params={"query": f"{title} {author}".strip(), "limit": 8, "fields": FIELDS},
        headers={"x-api-key": key},
        attempts=3,
    )
    return [_to_candidate(p) for p in (data or {}).get("data") or []]

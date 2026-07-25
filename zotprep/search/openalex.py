"""OpenAlex provider.

The most valuable single provider for this manuscript's mix: it indexes the
economics journals PubMed has never heard of, it indexes books, and crucially it
returns `biblio.{volume, issue, first_page, last_page}` — the fingerprint the
scorer needs to prove a match rather than guess at one.
"""
from __future__ import annotations

import re

import httpx

from ..models import Candidate, ParsedRef
from .base import get, query_terms

API = "https://api.openalex.org/works"

# OpenAlex maps its own work types onto Zotero item types.
TYPE_MAP = {
    "article": "journalArticle",
    "journal-article": "journalArticle",
    "book": "book",
    "book-chapter": "bookSection",
    "monograph": "book",
    "report": "report",
    "dissertation": "thesis",
    "preprint": "preprint",
    "proceedings-article": "conferencePaper",
    "paratext": "journalArticle",
    "editorial": "journalArticle",
    "letter": "journalArticle",
    "review": "journalArticle",
}


def _to_candidate(w: dict) -> Candidate:
    biblio = w.get("biblio") or {}
    src = ((w.get("primary_location") or {}).get("source")) or {}
    ids = w.get("ids") or {}
    pmid = ids.get("pmid")
    if pmid:
        pmid = pmid.rstrip("/").split("/")[-1]
    authors, corporate = [], None
    for a in (w.get("authorships") or [])[:60]:
        nm = ((a.get("author") or {}).get("display_name")) or a.get("raw_author_name") or ""
        if not nm:
            continue
        # OpenAlex sometimes stores consortium names in the author slot
        if re.search(r"\b(collaborators?|group|consortium|committee|network)\b", nm, re.I):
            corporate = corporate or nm
            continue
        from ..utils import surname_of

        authors.append(surname_of(nm))
    host = src.get("host_organization_name") or ""
    wtype = (w.get("type") or "").lower()
    return Candidate(
        provider="openalex",
        title=w.get("title") or w.get("display_name") or "",
        doi=w.get("doi"),
        pmid=pmid,
        authors=authors,
        corporate=corporate,
        year=w.get("publication_year"),
        journal=src.get("display_name") or "",
        journal_abbrev=src.get("abbreviated_title") or "",
        volume=biblio.get("volume"),
        issue=biblio.get("issue"),
        first_page=biblio.get("first_page"),
        last_page=biblio.get("last_page"),
        item_type=TYPE_MAP.get(wtype, "journalArticle"),
        publisher=host if wtype in ("book", "monograph", "book-chapter") else None,
        raw=w,
    )


def _sanitize_filter_value(s: str) -> str:
    """OpenAlex filter syntax uses , | : + as operators, so strip them out."""
    s = re.sub(r"[,|:+()\[\]{}]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


async def search(client: httpx.AsyncClient, ref: ParsedRef, mailto: str) -> list[Candidate]:
    title, author = query_terms(ref)
    if not title:
        return []
    out: list[Candidate] = []
    seen: set[str] = set()

    # Pass 1: title-field search — high precision.
    data = await get(
        client,
        "openalex",
        API,
        params={
            "filter": f"title.search:{_sanitize_filter_value(title)}",
            "per-page": 10,
            "mailto": mailto,
        },
    )
    # Pass 2 only when pass 1 came back empty. OpenAlex is metered now, so every
    # avoidable request is a request the rest of the bibliography gets to use.
    data2 = None
    if not ((data or {}).get("results")):
        data2 = await get(
            client,
            "openalex",
            API,
            params={"search": f"{title} {author}".strip(), "per-page": 10, "mailto": mailto},
        )
    for d in (data, data2):
        for w in (d or {}).get("results", []) or []:
            wid = w.get("id") or ""
            if wid in seen:
                continue
            seen.add(wid)
            out.append(_to_candidate(w))
    return out


async def by_identifier(
    client: httpx.AsyncClient, mailto: str, *, doi: str | None = None, pmid: str | None = None
) -> Candidate | None:
    ident = f"doi:{doi}" if doi else (f"pmid:{pmid}" if pmid else None)
    if not ident:
        return None
    data = await get(client, "openalex", f"{API}/{ident}", params={"mailto": mailto})
    return _to_candidate(data) if data else None

"""Crossref provider.

Used with `query.title` + `query.author` rather than `query.bibliographic`. The
old code's use of query.bibliographic on the whole reference string is precisely
what produced wrong #1 hits: journal abbreviations and page ranges become query
noise that Crossref's relevance scorer happily matches against other papers.

Crossref is also the metadata source of record once a DOI is known, so
`by_doi` is what fills in publisher/pages/ISSN for the Zotero item.
"""
from __future__ import annotations

import httpx

from ..models import Candidate, ParsedRef
from ..utils import surname_of
from .base import get, query_terms

API = "https://api.crossref.org/works"
# Only fields Crossref actually accepts in `select`. An unsupported name makes
# the whole request a 400, silently zeroing out this provider — keep this list in
# sync with https://api.crossref.org/works?rows=0 and do not add speculatively.
SELECT = (
    "DOI,title,author,issued,container-title,short-container-title,"
    "volume,issue,page,type,publisher,publisher-location,ISSN"
)

TYPE_MAP = {
    "journal-article": "journalArticle",
    "book": "book",
    "monograph": "book",
    "edited-book": "book",
    "reference-book": "book",
    "book-chapter": "bookSection",
    "book-section": "bookSection",
    "proceedings-article": "conferencePaper",
    "posted-content": "preprint",
    "report": "report",
    "dissertation": "thesis",
    "dataset": "dataset",
}


def to_candidate(w: dict) -> Candidate:
    authors, corporate = [], None
    for a in (w.get("author") or [])[:60]:
        if a.get("family"):
            authors.append(surname_of(f"{a.get('given','')} {a['family']}".strip()))
        elif a.get("name"):
            corporate = corporate or a["name"]
    dp = ((w.get("issued") or {}).get("date-parts") or [[None]])[0] or [None]
    page = w.get("page") or ""
    fp, _, lp = page.partition("-")
    ct = w.get("container-title") or []
    sct = w.get("short-container-title") or []
    item_type = TYPE_MAP.get(w.get("type", ""), "journalArticle")
    # For a chapter, Crossref's container-title is the *book* title, which Zotero
    # needs in its own bookTitle field rather than as a publication title.
    book_title = ct[0] if (ct and item_type == "bookSection") else ""
    return Candidate(
        provider="crossref",
        title=(w.get("title") or [""])[0] if w.get("title") else "",
        doi=w.get("DOI"),
        authors=authors,
        corporate=corporate,
        year=dp[0] if dp and isinstance(dp[0], int) else None,
        journal=ct[0] if ct else "",
        journal_abbrev=sct[0] if sct else "",
        volume=w.get("volume"),
        issue=w.get("issue"),
        first_page=fp.strip() or None,
        last_page=lp.strip() or None,
        item_type=item_type,
        book_title=book_title,
        publisher=w.get("publisher"),
        place=w.get("publisher-location"),
        raw=w,
    )


async def search(client: httpx.AsyncClient, ref: ParsedRef, mailto: str) -> list[Candidate]:
    title, author = query_terms(ref)
    if not title:
        return []
    params = {"query.title": title, "rows": 10, "select": SELECT, "mailto": mailto}
    if author:
        params["query.author"] = author
    data = await get(client, "crossref", API, params=params)
    items = ((data or {}).get("message") or {}).get("items") or []

    if not items:
        # Last resort only: bibliographic search over the reconstructed citation.
        # Deliberately not the primary query — its relevance ranking is what made
        # the previous version return confidently wrong papers.
        recon = " ".join(
            filter(None, [title, ref.journal, str(ref.year or ""), ref.volume, ref.first_page])
        )
        data = await get(
            client,
            "crossref",
            API,
            params={"query.bibliographic": recon, "rows": 8, "select": SELECT, "mailto": mailto},
        )
        items = ((data or {}).get("message") or {}).get("items") or []
    return [to_candidate(w) for w in items]


async def by_doi(client: httpx.AsyncClient, doi: str, mailto: str) -> Candidate | None:
    data = await get(client, "crossref", f"{API}/{doi}", params={"mailto": mailto})
    msg = (data or {}).get("message")
    return to_candidate(msg) if msg else None

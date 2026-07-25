"""Europe PMC provider.

Free, unmetered, no API key, and it returns exactly the fields the scorer wants:
`journalInfo.volume`, `journalInfo.issue`, `pageInfo`, plus
`journalInfo.journal.medlineAbbreviation` — which is the same NLM abbreviation
style Vancouver references already use, so journal matching becomes near-exact
rather than fuzzy.

Coverage is biomedical-plus (it also carries preprints and some Agricola/patent
records), so it complements Crossref rather than duplicating it.
"""
from __future__ import annotations

import re

import httpx

from ..models import Candidate, ParsedRef
from .base import get, query_terms

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

TYPE_MAP = {"book": "book", "bookish": "book", "preprint": "preprint"}


def _sanitize(s: str) -> str:
    """Strip Europe PMC query operators out of free text.

    Its query language treats : " ( ) [ ] { } ~ ^ ? * / and the bare words
    AND/OR/NOT as syntax. A title containing any of them silently returns zero
    hits rather than an error, which is how this provider quietly fails if you
    hand it a raw title.
    """
    s = re.sub(r"[^\w\s-]", " ", s or "")
    s = re.sub(r"\b(AND|OR|NOT)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _to_candidate(res: dict) -> Candidate:
    ji = res.get("journalInfo") or {}
    jr = ji.get("journal") or {}
    pages = res.get("pageInfo") or ""
    fp, _, lp = pages.partition("-")
    authors, corporate = [], None
    for a in ((res.get("authorList") or {}).get("author") or [])[:60]:
        if a.get("collectiveName"):
            corporate = corporate or a["collectiveName"]
        elif a.get("lastName"):
            authors.append(a["lastName"].lower())
    year = res.get("pubYear")
    return Candidate(
        provider="europepmc",
        title=(res.get("title") or "").rstrip("."),
        doi=res.get("doi"),
        pmid=res.get("pmid"),
        authors=authors,
        corporate=corporate,
        year=int(year) if str(year).isdigit() else None,
        journal=jr.get("title") or "",
        journal_abbrev=jr.get("medlineAbbreviation") or jr.get("isoabbreviation") or "",
        volume=ji.get("volume") or None,
        issue=ji.get("issue") or None,
        first_page=fp.strip() or None,
        last_page=lp.strip() or None,
        item_type=TYPE_MAP.get((res.get("bookOrReportDetails") and "book") or "", "journalArticle"),
        raw=res,
    )


async def _query(client: httpx.AsyncClient, q: str, size: int = 8) -> list[Candidate]:
    data = await get(
        client,
        "europepmc",
        API,
        params={"query": q, "format": "json", "resultType": "core", "pageSize": size},
    )
    results = ((data or {}).get("resultList") or {}).get("result") or []
    return [_to_candidate(r) for r in results]


async def search(client: httpx.AsyncClient, ref: ParsedRef, mailto: str) -> list[Candidate]:
    title, author = query_terms(ref)
    st = _sanitize(title)
    if not st:
        return []
    out: list[Candidate] = []

    # Pass 1: title field, phrase-quoted. High precision.
    q = f'TITLE:"{st}"'
    out += await _query(client, q)

    # Pass 2: only if the title phrase found nothing — avoids wasting a call.
    if not out:
        sa = _sanitize(author)
        q2 = f'"{st}"' + (f' AND AUTH:"{sa}"' if sa else "")
        out += await _query(client, q2)

    # Pass 3: the locator itself is a near-unique key when the title is odd.
    if not out and ref.journal and ref.year and ref.volume and ref.first_page:
        q3 = (
            f'JOURNAL:"{_sanitize(ref.journal)}" AND PUB_YEAR:{ref.year} '
            f'AND VOLUME:{_sanitize(ref.volume)}'
        )
        out += await _query(client, q3, size=25)
    return out


async def by_doi(client: httpx.AsyncClient, doi: str) -> Candidate | None:
    cands = await _query(client, f'DOI:"{doi}"', size=1)
    return cands[0] if cands else None

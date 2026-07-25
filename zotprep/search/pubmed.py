"""PubMed provider — deterministic first, free-text second.

The important piece is `ecitmatch`, NCBI's citation matcher. Given
journal|year|volume|first_page|author it returns the exact PMID or NOT_FOUND.
Because Vancouver references already carry NLM journal abbreviations, volume and
first page, this turns most biomedical references into a *lookup* rather than a
search — no relevance ranking, no ambiguity, nothing to score.

It also works with an empty author field, which is what rescues the consortium
references (GBD / India State-Level Collaborators) whose "first author" is not a
personal surname at all.
"""
from __future__ import annotations

import re

import httpx

from ..models import Candidate, ParsedRef
from ..utils import surname_of
from .base import get

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "zotprep"


def _to_candidate(rec: dict) -> Candidate:
    authors, corporate = [], None
    for a in rec.get("authors") or []:
        nm = a.get("name") or ""
        if not nm:
            continue
        if a.get("authtype") == "CollectiveName" or re.search(
            r"\b(collaborators?|group|consortium|committee|network)\b", nm, re.I
        ):
            corporate = corporate or nm
            continue
        authors.append(surname_of(nm))
    doi = next(
        (i["value"] for i in rec.get("articleids") or [] if i.get("idtype") == "doi"), None
    )
    ym = re.search(r"\b(19|20)\d{2}\b", rec.get("pubdate") or "")
    pages = rec.get("pages") or ""
    fp, _, lp = pages.partition("-")
    return Candidate(
        provider="pubmed",
        title=(rec.get("title") or "").rstrip("."),
        doi=doi,
        pmid=str(rec.get("uid") or "") or None,
        authors=authors,
        corporate=corporate,
        year=int(ym.group()) if ym else None,
        journal=rec.get("fulljournalname") or rec.get("source") or "",
        journal_abbrev=rec.get("source") or "",
        volume=rec.get("volume") or None,
        issue=rec.get("issue") or None,
        first_page=fp.strip() or None,
        last_page=lp.strip() or None,
        raw=rec,
    )


async def _summaries(client: httpx.AsyncClient, pmids: list[str], email: str) -> list[Candidate]:
    if not pmids:
        return []
    data = await get(
        client,
        "pubmed",
        f"{EUTILS}/esummary.fcgi",
        params={
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "json",
            "tool": TOOL,
            "email": email,
        },
    )
    result = (data or {}).get("result") or {}
    return [_to_candidate(result[p]) for p in pmids if isinstance(result.get(p), dict)]


async def citation_match(client: httpx.AsyncClient, ref: ParsedRef, email: str) -> str | None:
    """Deterministic PMID lookup from the reference's own locator fields.

    Tries with the first author and again with the author field blank, because
    the author spelling is the least reliable part of the tuple (initial order,
    accents, consortium names) while journal/year/volume/page are exact.
    """
    if not (ref.journal and ref.year and ref.volume and ref.first_page):
        return None
    author_variants = [""]
    if ref.lead_author:
        author_variants.insert(0, ref.lead_author)
    for author in author_variants:
        bdata = "|".join(
            [ref.journal, str(ref.year), ref.volume, ref.first_page, author, f"z{ref.n}"]
        ) + "|"
        text = await get(
            client,
            "pubmed",
            f"{EUTILS}/ecitmatch.cgi",
            params={"db": "pubmed", "retmode": "xml", "bdata": bdata},
            expect_json=False,
        )
        if not text:
            continue
        tail = text.strip().split("|")[-1].strip()
        if tail.isdigit():
            return tail
    return None


async def search(client: httpx.AsyncClient, ref: ParsedRef, email: str) -> list[Candidate]:
    """ecitmatch first; fall back to a title-field esearch."""
    pmids: list[str] = []
    exact = await citation_match(client, ref, email)
    if exact:
        pmids.append(exact)

    if ref.title:
        # [Title] restricts to the title field, which is far more precise than
        # the old code's untagged whole-reference term.
        term = f'"{ref.title}"[Title]'
        data = await get(
            client,
            "pubmed",
            f"{EUTILS}/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": term,
                "retmax": 8,
                "retmode": "json",
                "tool": TOOL,
                "email": email,
            },
        )
        found = ((data or {}).get("esearchresult") or {}).get("idlist") or []
        if not found:
            # loosen to all-fields when the exact title phrase isn't indexed
            data = await get(
                client,
                "pubmed",
                f"{EUTILS}/esearch.fcgi",
                params={
                    "db": "pubmed",
                    "term": f"{ref.title} {ref.lead_author or ''}".strip(),
                    "retmax": 8,
                    "retmode": "json",
                    "tool": TOOL,
                    "email": email,
                },
            )
            found = ((data or {}).get("esearchresult") or {}).get("idlist") or []
        pmids += [p for p in found if p not in pmids]

    cands = await _summaries(client, pmids[:10], email)
    for c in cands:
        if exact and c.pmid == exact:
            # flag for the scorer: this one came from the deterministic matcher
            c.provider = "pubmed:ecitmatch"
            c.providers = {"pubmed:ecitmatch", "pubmed"}
    return cands


async def by_pmid(client: httpx.AsyncClient, pmid: str, email: str) -> Candidate | None:
    cands = await _summaries(client, [pmid], email)
    return cands[0] if cands else None

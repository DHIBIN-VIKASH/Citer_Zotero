"""Orchestration: one ParsedRef in, one Resolution out.

Order of operations is deliberate and cheapest-first:

  1. correction store  — a human already decided this one
  2. identifier in ref — DOI/PMID/PMCID present, so no searching at all
  3. resolution cache  — same reference seen in a previous run
  4. provider fan-out  — four providers concurrently, title-based queries
  5. accept gate       — hard conjunctions, strongest tier wins
  6. book-from-text    — a Vancouver book reference already contains everything
                         Zotero needs, so build the item from the manuscript
"""
from __future__ import annotations

import asyncio

import httpx

from .cache import Cache
from .models import Candidate, ParsedRef, Resolution
from .scorer import ECITMATCH_TITLE_GUARD, accept, rank, signals
from .search import crossref, europepmc, openalex, pubmed, semanticscholar
from .search.base import dedupe

USER_AGENT = "zotprep/5.0 (biomedical reference resolver)"


def item_from_text(ref: ParsedRef) -> Candidate:
    """Build a Zotero item straight from the reference text.

    Not a guess: a complete Vancouver reference already contains every field
    Zotero needs. For books this is the *authoritative* path, since no API adds
    anything. For journal articles it is the honest fallback when no index holds
    the paper — some pre-1995 economics and humanities content simply has no DOI
    anywhere (J Econ Lit 1992 is JSTOR-era and absent from Crossref).

    This is exactly what a human does manually, so it is preferable to either
    flagging the reference or accepting a similar-looking wrong paper. Marked
    with its own provider name so the report never implies external corroboration.
    """
    return Candidate(
        provider="reference-text",
        title=ref.title,
        authors=list(ref.authors),
        corporate=ref.corporate,
        year=ref.year,
        journal=ref.journal,
        journal_abbrev=ref.journal,
        volume=ref.volume,
        issue=ref.issue,
        first_page=ref.first_page,
        last_page=ref.last_page,
        item_type=("bookSection" if ref.is_chapter else "book") if ref.is_book else "journalArticle",
        book_title=ref.book_title,
        publisher=ref.publisher,
        place=ref.place,
        raw={"edition": ref.edition, "source": "parsed from reference text"},
    )


# Back-compat alias; the book path was the original caller.
book_from_text = item_from_text


def complete_enough_for_text_item(ref: ParsedRef) -> bool:
    """Guard against fabricating an item from a reference we failed to parse.

    A book needs title + publisher; an article needs title + journal + year and
    at least a volume or a first page. Anything less goes to human review rather
    than into the library.
    """
    if not ref.title or len(ref.title) < 8:
        return False
    if ref.is_chapter:
        return bool(ref.book_title and ref.year)
    if ref.is_book:
        return bool(ref.publisher and ref.year)
    return bool(ref.journal and ref.year and (ref.volume or ref.first_page))


def _length_ratio(a: str, b: str) -> float:
    """Token-count ratio of two titles, 0..1."""
    from .utils import norm_text

    ta, tb = norm_text(a).split(), norm_text(b).split()
    if not ta or not tb:
        return 0.0
    return min(len(ta), len(tb)) / max(len(ta), len(tb))


def _author_related(ref: ParsedRef, cand: Candidate) -> bool:
    """Loose surname agreement, to avoid crying fabrication over name variants.

    Bibliographies abbreviate compound surnames ("Mendez" for "Mendez-Guerra")
    and providers vary on hyphenation and particles, so exact set membership is
    too strict a basis for an accusatory advisory.
    """
    ref_names = [a.lower() for a in ref.authors if a]
    cand_names = [a.lower() for a in cand.authors if a]
    for r in ref_names:
        for c in cand_names:
            if r.startswith(c) or c.startswith(r):
                return True
    return False


def locator_notes(ref: ParsedRef, ranked) -> list[str]:
    """Advisories about a reference whose own fields disagree with each other.

    Two distinct problems both show up as a citation-matcher hit whose title does
    not match, and they need different wording:

      * The locator resolves to a real, *related* paper — usually a sibling in the
        same journal issue, meaning the volume/pages were copied from a
        neighbouring reference. Reference 13 of the osteoporosis manuscript names
        chronic kidney disease while its pages point at the chronic respiratory
        diseases paper in the same issue.
      * The locator resolves to something unrelated, which more often means the
        journal name itself is wrong, or the "page" is an article number the
        matcher indexes differently.

    These are advisories, never blockers: the title is what the author meant, and
    the correct paper is usually found by title anyway.
    """
    notes: list[str] = []

    # Strongest signal of a bad reference: some real paper carries this exact
    # title, but with different authors in a different journal. A genuine
    # miscitation usually gets the authors right and fumbles the volume; a title
    # attached to the wrong authors *and* the wrong journal is characteristic of a
    # fabricated entry, and the named authors are typically real researchers in
    # the field, which is what makes it invisible on a read-through.
    for cand, sg, _s in ranked:
        # Comparable title lengths are required *here* — this is the one place a
        # length check is safe. A short reference title ("Economic Growth") is a
        # subset of countless longer papers, so without it the advisory accuses
        # every book of being fabricated.
        if (
            sg.title_sim >= 0.95
            and _length_ratio(ref.title, cand.title) >= 0.70
            and not sg.author_ok
            and sg.author_overlap == 0.0
            and not _author_related(ref, cand)
            and not sg.journal_ok
            and cand.doi
        ):
            notes.append(
                f"VERIFY THIS ENTRY: the title matches a real paper "
                f"({cand.doi}, {cand.journal or '?'} {cand.year or '?'}) whose authors "
                f"({', '.join(a.title() for a in cand.authors[:3]) or '?'}) and journal "
                f"differ entirely from this reference ({ref.lead_author or ref.corporate}, "
                f"{ref.journal}). Confirm the reference exists as cited."
            )
            break

    for _c, sg, _s in ranked:
        if not (sg.ecitmatch and sg.title_sim < ECITMATCH_TITLE_GUARD):
            continue
        if sg.title_sim >= 0.60:
            notes.append(
                f"reference's journal/volume/pages ({ref.journal} {ref.year};"
                f"{ref.volume}:{ref.first_page}) point to a DIFFERENT paper than its "
                "title — the volume/pages were probably copied from a neighbouring "
                "reference. Verify this entry."
            )
        else:
            notes.append(
                f"nothing at {ref.journal} {ref.year};{ref.volume}:{ref.first_page} "
                "matches this title — check the journal name and volume/page numbers."
            )
        break
    return notes


async def _gather_candidates(
    client: httpx.AsyncClient, ref: ParsedRef, mailto: str
) -> list[Candidate]:
    tasks = [
        crossref.search(client, ref, mailto),
        europepmc.search(client, ref, mailto),
        pubmed.search(client, ref, mailto),
        openalex.search(client, ref, mailto),
        semanticscholar.search(client, ref, mailto),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    cands: list[Candidate] = []
    for r in results:
        if isinstance(r, BaseException):
            continue
        cands += r
    return dedupe(cands)


async def _resolve_identifier(
    client: httpx.AsyncClient, ref: ParsedRef, mailto: str
) -> Candidate | None:
    """Deterministic path. Crossref is preferred for DOIs because its record is
    the most complete for Zotero's fields; OpenAlex covers PMID/PMCID lookups."""
    if ref.doi:
        c = await crossref.by_doi(client, ref.doi, mailto)
        if c:
            return c
        c = await openalex.by_identifier(client, mailto, doi=ref.doi)
        if c:
            return c
    if ref.pmid:
        c = await pubmed.by_pmid(client, ref.pmid, mailto)
        if c:
            return c
        c = await openalex.by_identifier(client, mailto, pmid=ref.pmid)
        if c:
            return c
    return None


async def resolve_one(
    client: httpx.AsyncClient, ref: ParsedRef, mailto: str, cache: Cache
) -> Resolution:
    # 1. a human already told us the answer
    fixed = cache.get_correction(ref)
    if fixed:
        return Resolution(
            n=ref.n, status="ACCEPTED", confidence=1.0, tier="correction", candidate=fixed
        )

    # 2. the reference carries an identifier
    if ref.has_identifier:
        c = await _resolve_identifier(client, ref, mailto)
        if c:
            return Resolution(
                n=ref.n, status="ACCEPTED", confidence=1.0, tier="identifier", candidate=c
            )

    # 3. previously resolved
    hit = cache.get(ref)
    if hit and hit.candidate:
        return hit

    # 4. search
    cands = await _gather_candidates(client, ref, mailto)
    ranked = rank(ref, cands)

    # 5. accept gate, strongest tier first across all candidates
    best: tuple[float, str, Candidate] | None = None
    for cand, sg, _conf in ranked:
        ok, tier, conf = accept(ref, cand, sg)
        if ok and (best is None or conf > best[0]):
            best = (conf, tier, cand)
    if best:
        conf, tier, cand = best
        res = Resolution(
            n=ref.n,
            status="ACCEPTED",
            confidence=conf,
            tier=tier,
            candidate=cand,
            alternatives=[c for c, _, _ in ranked[:4] if c is not cand],
            signals=signals(ref, cand).__dict__,
            # No locator advisory here: the reference was corroborated by a real
            # tier, so a disagreeing citation-matcher hit is just ecitmatch noise
            # (common for journals that number articles rather than pages).
        )
        cache.put(ref, res)
        return res

    # 6. nothing external holds this paper, but the reference is fully parsed
    if complete_enough_for_text_item(ref):
        kind = "book" if ref.is_book else "article"
        res = Resolution(
            n=ref.n,
            status="FROM_TEXT",
            confidence=0.80,
            tier="from-reference-text",
            candidate=item_from_text(ref),
            alternatives=[c for c, _, _ in ranked[:3]],
            reason=(f"no index holds this {kind}; item built from the reference text "
                    "(no external corroboration)"),
            notes=locator_notes(ref, ranked),
        )
        return res

    reason = "no candidates returned by any provider"
    if ranked:
        top_sig = ranked[0][1]
        reason = "best candidate rejected: " + "; ".join(top_sig.failing())
    return Resolution(
        n=ref.n,
        status="REVIEW",
        confidence=ranked[0][2] if ranked else 0.0,
        tier="none",
        candidate=None,
        alternatives=[c for c, _, _ in ranked[:5]],
        reason=reason,
    )


async def resolve_all(
    refs: dict[int, ParsedRef],
    mailto: str,
    cache: Cache,
    workers: int = 16,
    progress=None,
) -> dict[int, Resolution]:
    limits = httpx.Limits(max_connections=workers * 2, max_keepalive_connections=workers)
    timeout = httpx.Timeout(30.0, connect=15.0)
    gate = asyncio.Semaphore(workers)
    out: dict[int, Resolution] = {}

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        limits=limits,
        timeout=timeout,
        follow_redirects=True,
    ) as client:

        async def one(ref: ParsedRef) -> None:
            async with gate:
                out[ref.n] = await resolve_one(client, ref, mailto, cache)
                if progress:
                    progress(out[ref.n])

        await asyncio.gather(*(one(refs[n]) for n in sorted(refs)))
    return out

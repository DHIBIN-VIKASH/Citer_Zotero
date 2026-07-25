"""Candidate scoring and the accept/reject gate.

Design note, because this is the file that fixes the original bug:

The old scorer computed one fuzzy ratio between the raw reference string and a
reconstructed "author year title" string, then added/subtracted flat bonuses.
That number is dominated by how much *boilerplate* the two strings share
(journal abbreviation, volume, page range), not by whether they describe the same
paper — so it hovered around 0.35-0.55 for correct and incorrect matches alike,
and a 0.55 threshold admitted wrong papers.

The replacement separates two jobs that must not be conflated:

  * `confidence()` produces a number, used only to *rank* candidates.
  * `accept()` produces a decision, using hard gates on independent signals.

A wrong paper can fake one signal. It cannot simultaneously fake the title, the
year, and the volume/first-page pair — that combination is effectively a unique
key. So acceptance requires a conjunction, never a weighted sum crossing a line.
Anything that fails every gate is routed to human review rather than guessed at.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Candidate, ParsedRef
from .utils import journal_match, norm_text, page_equal, title_similarity, volume_equal

# Aggregators that mirror publisher records rather than host them. When a
# duplicate pair is otherwise tied, prefer the publisher's own DOI.
SECONDARY_DOI_PREFIXES = ("10.2307/",)

# Minimum title agreement before a citation-matcher hit is trusted. Measured
# across two manuscripts, 42 of 43 genuine ecitmatch hits scored >= 0.98, while a
# reference whose volume/pages had been copied from a sibling paper in the same
# journal issue scored 0.83 — so this cleanly separates "title formatted
# differently" from "this is a different paper".
ECITMATCH_TITLE_GUARD = 0.92


@dataclass
class Signals:
    title_sim: float = 0.0
    year_delta: int | None = None
    author_ok: bool = False
    author_overlap: float = 0.0
    corporate_ok: bool = False
    journal_ok: bool = False
    volume_ok: bool = False
    first_page_ok: bool = False
    last_page_ok: bool = False
    type_ok: bool = True
    n_providers: int = 1
    ecitmatch: bool = False

    @property
    def locator_ok(self) -> bool:
        return self.volume_ok and self.first_page_ok

    @property
    def year_ok(self) -> bool:
        return self.year_delta is not None and self.year_delta <= 1

    def failing(self) -> list[str]:
        """Human-readable reasons, for the review report."""
        out = []
        if self.ecitmatch and self.title_sim < ECITMATCH_TITLE_GUARD:
            out.append(
                "THE REFERENCE CONTRADICTS ITSELF: its journal/year/volume/pages "
                "identify a different paper than its title. Check the bibliography "
                "entry — the volume or page numbers were likely copied from a "
                "neighbouring reference"
            )
        if self.title_sim < 0.90:
            out.append(f"title similarity {self.title_sim:.2f}")
        if self.year_delta is None:
            out.append("candidate has no year")
        elif self.year_delta > 1:
            out.append(f"year differs by {self.year_delta}")
        if not self.locator_ok:
            missing = []
            if not self.volume_ok:
                missing.append("volume")
            if not self.first_page_ok:
                missing.append("first page")
            out.append("no " + "/".join(missing) + " match")
        if not self.journal_ok:
            out.append("journal mismatch")
        if not (self.author_ok or self.corporate_ok):
            out.append("author mismatch")
        return out


def signals(ref: ParsedRef, cand: Candidate) -> Signals:
    s = Signals()
    s.title_sim = title_similarity(ref.title, cand.title)
    if ref.year and cand.year:
        s.year_delta = abs(ref.year - cand.year)

    ref_surnames = {a.lower() for a in (ref.authors or []) if a}
    cand_surnames = {a.lower() for a in (cand.authors or []) if a}
    if ref_surnames and cand_surnames:
        hits = sum(1 for a in ref_surnames if a in cand_surnames)
        s.author_overlap = hits / len(ref_surnames)
        # the reference's lead author must appear somewhere in the candidate's
        # author list; position can differ across providers
        lead = (ref.lead_author or "").lower()
        s.author_ok = bool(lead) and lead in cand_surnames

    if ref.corporate:
        pool = " ".join(filter(None, [cand.corporate or "", cand.title or ""]))
        s.corporate_ok = title_similarity(ref.corporate, cand.corporate or "") >= 0.80 or (
            _consortium_key(ref.corporate) in norm_text(pool)
        )

    s.journal_ok = bool(ref.journal) and journal_match(
        ref.journal, cand.journal, cand.journal_abbrev
    )
    s.volume_ok = volume_equal(ref.volume, cand.volume)
    s.first_page_ok = page_equal(ref.first_page, cand.first_page)
    s.last_page_ok = page_equal(ref.last_page, cand.last_page)

    if ref.is_book:
        s.type_ok = cand.item_type in ("book", "bookSection")
    s.n_providers = len({p.split(":")[0] for p in (cand.providers or {cand.provider})})
    s.ecitmatch = "pubmed:ecitmatch" in (cand.providers or set())
    return s


def _consortium_key(corporate: str) -> str:
    """Distinctive part of a consortium name, for substring probing.

    "GBD 2021 Low Back Pain Collaborators" -> "gbd 2021 low back pain".
    The generic tail words are dropped because providers render them
    inconsistently or omit them entirely.
    """
    txt = norm_text(corporate)
    for tail in ("collaborators", "collaborator", "collaboration", "group", "consortium",
                 "committee", "investigators", "network"):
        txt = txt.replace(tail, " ")
    return " ".join(txt.split())


def confidence(s: Signals) -> float:
    """Ranking score only. Never used on its own to accept a match."""
    score = 0.42 * s.title_sim
    if s.year_delta is not None:
        score += 0.14 if s.year_delta == 0 else (0.07 if s.year_delta == 1 else 0.0)
    score += 0.10 if s.author_ok else 0.06 * s.author_overlap
    score += 0.04 if s.corporate_ok else 0.0
    score += 0.09 if s.journal_ok else 0.0
    score += 0.09 if s.locator_ok else (0.04 if s.volume_ok or s.first_page_ok else 0.0)
    score += 0.04 if s.last_page_ok else 0.0
    score += min(0.05, 0.025 * (s.n_providers - 1))
    score += 0.03 if s.ecitmatch else 0.0
    if not s.type_ok:
        score -= 0.15
    return max(0.0, min(1.0, score))


def accept(ref: ParsedRef, cand: Candidate, s: Signals) -> tuple[bool, str, float]:
    """Hard gates. Returns (accepted, tier_name, confidence_for_that_tier).

    Ordered strongest-first. Each gate is a conjunction of independent signals,
    so passing requires the candidate to agree with the reference on several
    facts that a merely similar paper would not share.
    """
    # NCBI's citation matcher keyed on journal+year+volume+first-page. Collisions
    # are essentially impossible, so a title disagreement here means the
    # *reference itself* is internally inconsistent: its locator points at one
    # paper while its title names another. That is a manuscript error, and it must
    # surface rather than resolve to whichever paper the locator happens to hit.
    if s.ecitmatch and s.title_sim >= ECITMATCH_TITLE_GUARD:
        return True, "ecitmatch", 0.99

    # Title + year + exact volume/first-page. The fingerprint gate.
    if s.title_sim >= 0.90 and s.year_ok and s.locator_ok and s.type_ok:
        return True, "fingerprint", 0.97

    # No locator available anywhere (older/econ records, books): demand a
    # near-exact title plus both journal and author agreement instead.
    if s.title_sim >= 0.93 and s.year_ok and s.journal_ok and (s.author_ok or s.corporate_ok) and s.type_ok:
        return True, "title+journal+author", 0.93

    # Independent providers converged on the same DOI.
    if s.n_providers >= 2 and s.title_sim >= 0.88 and s.year_ok and (
        s.journal_ok or s.author_ok or s.corporate_ok
    ) and s.type_ok:
        return True, "provider-agreement", 0.90

    # Exact normalized title match with the right year and author. Used mostly
    # for books, where no provider supplies volume/page and journal is absent.
    if (
        norm_text(ref.title)
        and norm_text(ref.title) == norm_text(cand.title)
        and s.year_ok
        and (s.author_ok or s.corporate_ok)
        and s.type_ok
    ):
        return True, "exact-title", 0.88

    return False, "none", confidence(s)


def rank(ref: ParsedRef, cands: list[Candidate]) -> list[tuple[Candidate, Signals, float]]:
    scored = [(c, sg := signals(ref, c), confidence(sg)) for c in cands]
    scored.sort(key=lambda t: (t[2], _tiebreak(t[0], t[1])), reverse=True)
    return scored


def _tiebreak(cand: Candidate, s: Signals) -> tuple:
    """Separate true duplicates of the same paper (e.g. publisher DOI vs JSTOR)."""
    secondary = (cand.doi or "").startswith(SECONDARY_DOI_PREFIXES)
    return (
        s.last_page_ok,
        s.journal_ok,
        not secondary,
        s.n_providers,
        bool(cand.doi),
        bool(cand.pmid),
    )

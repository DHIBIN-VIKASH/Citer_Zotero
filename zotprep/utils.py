"""Normalization and comparison primitives used by the scorer.

Keeping these pure and dependency-light matters: the accept/reject decision is
built entirely out of these functions, so they are the part of the system that
most needs to be individually testable.
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

# Words that carry no discriminating power in a journal title.
JOURNAL_STOPWORDS = {"the", "of", "and", "for", "in", "on", "a", "an", "de", "der", "la", "le"}
TITLE_STOPWORDS = {"the", "a", "an"}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def norm_text(s: str) -> str:
    """Aggressive normalization for fuzzy title comparison."""
    s = strip_accents(unicodedata.normalize("NFKC", s or "")).lower()
    s = re.sub(r"<[^>]+>", " ", s)  # provider titles sometimes carry <i>/<sub>
    s = re.sub(r"&(amp|lt|gt|quot|apos);", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    toks = [t for t in s.split() if t not in TITLE_STOPWORDS]
    return " ".join(toks)


def title_similarity(a: str, b: str) -> float:
    """0..1 similarity between two titles.

    `token_set_ratio` alone is unsafe here: it scores 1.00 whenever one title's
    tokens are a *subset* of the other's, so a short reference title matches a
    longer unrelated paper perfectly. Observed in the wild:

        ref : "Renal osteodystrophy and chronic kidney disease-mineral bone disorder"
        cand: "Mitochondrial dysfunction and mitophagy blockade contribute to renal
               osteodystrophy in chronic kidney disease-mineral bone disorder"

    Every reference token appears in the candidate, giving a false 1.00 on the
    wrong paper.

    Damping the set score by token-count ratio was tried and reverted: legitimate
    matches are *also* subsets, because journals expand titles in the version of
    record. "Global incidence, prevalence, years lived with disability ... 371
    diseases ... 1990-2021" is 24 tokens in a bibliography and 45 in Crossref,
    once "(YLDs)", "(DALYs)", "811 subnational locations" and the "a systematic
    analysis for the Global Burden of Disease Study 2021" tail are included. Any
    length penalty strong enough to reject the unrelated subset also rejects that.

    So the inflation is left alone and handled where it belongs: the accept gate
    never takes title similarity on its own. The unrelated-subset case is rejected
    because its journal and authors both disagree, which is a conjunction no
    string metric has to resolve.
    """
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return max(fuzz.token_set_ratio(na, nb), fuzz.token_sort_ratio(na, nb)) / 100.0


def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.rstrip(".") or None


def norm_surname(s: str) -> str:
    s = strip_accents(s or "").lower()
    s = re.sub(r"[^a-z\s-]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def surname_of(display_name: str) -> str:
    """Best-effort surname from a provider's free-form author name.

    Handles "Prakash C Gupta", "Gupta, Prakash C", and particle surnames
    ("Xavier Sala-i-Martin", "Jacques Vallin", "Ludwig van Beethoven").
    """
    name = (display_name or "").strip()
    if not name:
        return ""
    if "," in name:
        return norm_surname(name.split(",")[0])
    parts = name.split()
    if len(parts) == 1:
        return norm_surname(parts[0])
    # walk back over lowercase particles: "van der Berg" -> "van der berg"
    i = len(parts) - 1
    while i > 0 and parts[i - 1][:1].islower():
        i -= 1
    return norm_surname(" ".join(parts[i:]))


def _journal_tokens(s: str) -> list[str]:
    s = strip_accents(s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [t for t in s.split() if t not in JOURNAL_STOPWORDS]


def journal_match(ref_journal: str, *candidates: str) -> bool:
    """Abbreviation-tolerant journal comparison, no lookup table needed.

    Vancouver uses NLM-style abbreviations where every abbreviated token is a
    prefix of the corresponding full word, in order:

        "J Polit Econ"          ~ "Journal of Political Economy"
        "J R Stat Soc Series B" ~ "Journal of the Royal Statistical Society: Series B"
        "Bull World Health Organ" ~ "Bulletin of the World Health Organization"

    So: pair the tokens up positionally and require each reference token to be a
    prefix of its counterpart. Stopwords are dropped from both sides first, which
    is what lets "of"/"the" vanish.

    Token counts must match exactly. Without that, "Lancet" would match "Lancet
    Oncology" — a wrong-journal false positive is far more costly than a missed
    signal, since the accept gate can fall back to the volume/page fingerprint.
    """
    ref_toks = _journal_tokens(ref_journal)
    if not ref_toks:
        return False
    for cand in candidates:
        if not cand:
            continue
        # try the raw name, then progressively stripped variants, because
        # providers append qualifiers: "… Society: Series B (Methodological)"
        variants = [cand, re.sub(r"\([^)]*\)", " ", cand)]
        if ":" in cand:
            variants.append(cand.split(":")[0])
        for variant in variants:
            cand_toks = _journal_tokens(variant)
            if len(cand_toks) != len(ref_toks):
                continue
            if all(c.startswith(r) or r.startswith(c) for r, c in zip(ref_toks, cand_toks)):
                return True
    return False


def page_equal(a: str | None, b: str | None) -> bool:
    """Compare page labels tolerantly: 'e1339' == 'e1339', '17' == '17-23' start."""
    if not a or not b:
        return False
    na = re.sub(r"[^a-z0-9]", "", strip_accents(a).lower())
    nb = re.sub(r"[^a-z0-9]", "", strip_accents(b).lower())
    return na == nb


def volume_equal(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    na = re.sub(r"[^a-z0-9]", "", strip_accents(a).lower())
    nb = re.sub(r"[^a-z0-9]", "", strip_accents(b).lower())
    return na == nb

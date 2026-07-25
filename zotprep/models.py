"""Data structures shared across the resolution pipeline.

Three objects move through the pipeline:

    raw reference string  --extractor-->  ParsedRef
    ParsedRef             --providers-->  [Candidate]
    ParsedRef+Candidate   --scorer----->  Resolution
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ParsedRef:
    """Everything we could pull out of the raw reference text itself.

    This is ground truth: it comes from the manuscript, not from an API, so the
    scorer treats it as the thing candidates are measured against.
    """

    n: int
    raw: str
    # deterministic identifiers found in the text
    doi: Optional[str] = None
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    # bibliographic fields
    authors: list[str] = field(default_factory=list)  # surnames, in order
    corporate: Optional[str] = None  # "GBD 2021 Diseases and Injuries Collaborators"
    title: str = ""
    journal: str = ""  # as abbreviated in the reference
    year: Optional[int] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    first_page: Optional[str] = None
    last_page: Optional[str] = None
    # book-shaped references
    is_book: bool = False
    is_chapter: bool = False  # "In: <editors>, eds. <book title>" -> bookSection
    book_title: str = ""  # containing volume, when is_chapter
    publisher: Optional[str] = None
    place: Optional[str] = None
    edition: Optional[str] = None

    @property
    def has_identifier(self) -> bool:
        return bool(self.doi or self.pmid or self.pmcid)

    @property
    def lead_author(self) -> Optional[str]:
        """First personal-author surname, or None for consortium references."""
        return self.authors[0] if self.authors else None


@dataclass
class Candidate:
    """A normalized search hit from any provider."""

    provider: str
    title: str = ""
    doi: Optional[str] = None
    pmid: Optional[str] = None
    authors: list[str] = field(default_factory=list)  # surnames
    corporate: Optional[str] = None
    year: Optional[int] = None
    journal: str = ""  # full container title
    journal_abbrev: str = ""  # provider-supplied abbreviation, when available
    volume: Optional[str] = None
    issue: Optional[str] = None
    first_page: Optional[str] = None
    last_page: Optional[str] = None
    item_type: str = "journalArticle"  # Zotero item type
    book_title: str = ""  # containing volume, for bookSection
    publisher: Optional[str] = None
    place: Optional[str] = None
    raw: Any = None
    # filled in during aggregation
    providers: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.providers:
            self.providers = {self.provider}


@dataclass
class Resolution:
    """Outcome for one reference."""

    n: int
    status: str  # "ACCEPTED" | "REVIEW" | "MANUAL"
    confidence: float
    tier: str  # which rule fired, e.g. "identifier", "ecitmatch", "fingerprint"
    candidate: Optional[Candidate] = None
    alternatives: list[Candidate] = field(default_factory=list)
    reason: str = ""  # why it failed / which signal disagreed
    notes: list[str] = field(default_factory=list)  # advisories worth the author's eye
    signals: dict[str, Any] = field(default_factory=dict)
    zotero_key: Optional[str] = None

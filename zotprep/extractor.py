"""Turn a raw reference string into a ParsedRef.

The whole accuracy strategy rests on this file. Everything downstream compares
candidates against these extracted fields, so a bad parse here becomes a bad
match later. Deliberately conservative: when a field cannot be extracted with
confidence it is left None rather than guessed, because the scorer treats a
missing signal as neutral but a wrong signal as evidence.

Handles Vancouver (primary), plus APA/AMA-ish and book forms:

    Barro RJ, Sala-i-Martin X. Convergence. J Polit Econ. 1992;100(2):223-51.
    GBD 2021 Collaborators. Global incidence ... Lancet. 2024.
    Theil H. Economics and Information Theory. Amsterdam: North-Holland; 1967.
    Cowell FA. Measuring Inequality. 3rd ed. Oxford: Oxford University Press; 2011.
"""
from __future__ import annotations

import re
import unicodedata

from .models import ParsedRef

# --- identifier patterns -----------------------------------------------------
# DOI: trailing punctuation is stripped afterwards, since refs end in "."
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>,;]+", re.I)
DOI_TRAILING = re.compile(r"[.,;:)\]}>'\"]+$")
PMID_RE = re.compile(r"\bPMID:?\s*(\d{4,9})\b", re.I)
PMCID_RE = re.compile(r"\b(PMC\d{4,9})\b", re.I)

# --- structural patterns -----------------------------------------------------
LEAD_NUM = re.compile(r"^\s*[\[(]?\s*(\d{1,3})\s*[.)\]]\s+")
BRACKET_NOTE = re.compile(r"\[[^\]]*\]")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

# "2017;390(10111):2437-60"  /  "2012;10:1"  /  "2004;S2:11-44"
#
# Two shapes that are ordinary in medical Vancouver and that a tighter pattern
# misses entirely — and missing the locator does not degrade the match, it
# removes it: journal, volume and first page all come back empty, so the accept
# gate has nothing left to agree on and the reference is rejected.
#
#   "2011;63 Suppl 11:S240-52"   a supplement, with a space inside the volume
#   "2016;10(9):UC05-7"          a first page with two letters, not one
#   "2018;100-B(8):991-1001"     a lettered volume, as the Bone & Joint
#                                Journal numbers them — and Crossref stores it
#                                that way too, so the suffix is kept
#
# The supplement is captured so it cannot be mistaken for part of the volume,
# and then dropped: indexes store the base volume ("63"), which is what the
# volume comparison needs to see.
LOCATOR_RE = re.compile(
    r"\b(?P<year>(?:19|20)\d{2})\s*;\s*"
    r"(?P<vol>[A-Za-z]?[\dA-Za-z]{0,8}?(?:-[A-Za-z]{1,2})?)"
    r"(?:\s*(?P<suppl>[Ss]uppl(?:ement)?\.?\s*[\dA-Za-z]{0,4}))?\s*"
    r"(?:\(\s*(?P<issue>[^)]{1,20})\s*\))?\s*"
    r":\s*(?P<fp>[A-Za-z]{0,3}\d+)(?:\s*-\s*(?P<lp>[A-Za-z]{0,3}\d+))?"
)
# Book imprint tail. Both separators before the year occur in the wild:
#   "Amsterdam: North-Holland; 1967"   (Vancouver)
#   "Amsterdam: North-Holland, 1967"   (Lancet house style)
#   "Amsterdam: Elsevier, 2013: 1113-36"  (chapter, with page range)
BOOK_TAIL_RE = re.compile(
    r"^(?P<place>[^:;]{2,40}):\s*(?P<publisher>[^;,]{2,60})[;,]\s*"
    r"(?P<year>(?:19|20)\d{2})"
    r"(?:\s*:\s*(?P<fp>[A-Za-z]?\d+)(?:\s*-\s*(?P<lp>[A-Za-z]?\d+))?)?\.?$"
)
EDITION_RE = re.compile(r"^(?P<ed>\d+(?:st|nd|rd|th)|rev(?:ised)?|\d+)\s*edn?\.?$", re.I)
# trailing ", 4th edn" attached to a book title rather than standing alone
INLINE_EDITION_RE = re.compile(r",\s*(?P<ed>\d+(?:st|nd|rd|th)|rev(?:ised)?)\s*edn?\.?\s*$", re.I)
CHAPTER_IN_RE = re.compile(r"^In:\s*(?P<editors>.+?),?\s*eds?\.?$", re.I)

CORPORATE_HINT = re.compile(
    r"\b(collaborators?|collaboration|group|consortium|committee|initiative|"
    r"investigators|network|study team|working party|organization|organisation|"
    r"who|unicef|world health)\b",
    re.I,
)

# A segment is an author list if it looks like "Surname AB, Surname CD" —
# i.e. mostly "word + 1-4 capital initials" tokens.
AUTHOR_TOKEN = re.compile(
    r"^(?P<sur>(?:[A-Z][\w'’-]*(?:\s+[a-z]{2,3})?(?:[-\s][A-Z][\w'’-]*)*))"
    r"\s+(?P<init>(?:[A-Z]\.?){1,4})$"
)
SENTENCE_SPLIT = re.compile(r"(?<=[.?!])\s+")


def _nfkc(s: str) -> str:
    """Normalize unicode and unify the dash/quote zoo found in pasted refs."""
    s = unicodedata.normalize("NFKC", s)
    for bad in "‐‑‒–—―−":
        s = s.replace(bad, "-")
    for bad, good in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"')):
        s = s.replace(bad, good)
    return re.sub(r"\s+", " ", s).strip()


def expand_page(first: str | None, last: str | None) -> str | None:
    """Vancouver abbreviates end pages: 2437-60 means 2437-2460, e1339-51 -> e1351."""
    if not first or not last:
        return last
    pre_f = re.match(r"^([A-Za-z]*)(\d+)$", first)
    pre_l = re.match(r"^([A-Za-z]*)(\d+)$", last)
    if not (pre_f and pre_l):
        return last
    fdig, ldig = pre_f.group(2), pre_l.group(2)
    if len(ldig) < len(fdig):
        ldig = fdig[: len(fdig) - len(ldig)] + ldig
    return (pre_l.group(1) or pre_f.group(1)) + ldig


def _split_segments(text: str) -> list[str]:
    """Split a reference into sentence-ish segments.

    Vancouver puts a period after the last author's initials and after "et al.",
    so a plain sentence split lands exactly on the author/title/journal
    boundaries. Mid-list authors are separated by commas, not periods, so they
    survive the split intact.
    """
    return [p.strip() for p in SENTENCE_SPLIT.split(text) if p.strip()]


def _looks_like_authors(seg: str) -> bool:
    seg = seg.rstrip(".")
    seg = re.sub(r",?\s*et al\.?$", "", seg, flags=re.I)
    chunks = [c.strip() for c in seg.split(",") if c.strip()]
    if not chunks:
        return False
    hits = sum(1 for c in chunks if AUTHOR_TOKEN.match(c))
    return hits >= max(1, len(chunks) // 2)


def _parse_authors(seg: str) -> tuple[list[str], str | None]:
    """Return (personal surnames, corporate name or None)."""
    seg = seg.strip().rstrip(".")
    seg = re.sub(r",?\s*et al\.?$", "", seg, flags=re.I)
    if CORPORATE_HINT.search(seg) and not _looks_like_authors(seg + "."):
        return [], seg
    surnames: list[str] = []
    for chunk in seg.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = AUTHOR_TOKEN.match(chunk)
        if m:
            surnames.append(m.group("sur").strip())
        else:
            # "Surname, Given" (APA) or an un-initialled name
            word = chunk.split()[0] if chunk.split() else ""
            if word[:1].isupper() and len(word) > 1 and not re.fullmatch(r"(?:[A-Z]\.?){1,4}", word):
                surnames.append(word)
    if not surnames and CORPORATE_HINT.search(seg):
        return [], seg
    return surnames, None


def parse_reference(n: int, raw: str) -> ParsedRef:
    ref = ParsedRef(n=n, raw=raw.strip())
    text = _nfkc(raw)
    text = LEAD_NUM.sub("", text)

    # --- identifiers: pull, then remove so they don't pollute segmentation ---
    m = DOI_RE.search(text)
    if m:
        ref.doi = DOI_TRAILING.sub("", m.group(0)).lower()
        text = text.replace(m.group(0), " ")
    m = PMID_RE.search(text)
    if m:
        ref.pmid = m.group(1)
        text = PMID_RE.sub(" ", text)
    m = PMCID_RE.search(text)
    if m:
        ref.pmcid = m.group(1).upper()
        text = PMCID_RE.sub(" ", text)

    # editorial annotations like "[Zotero: complete vol/pages/DOI]" are noise
    text = BRACKET_NOTE.sub(" ", text)
    text = re.sub(r"\b(?:doi|available from|accessed)\b[:.]?\s*", " ", text, flags=re.I)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # --- locator (journal;volume(issue):pages) -------------------------------
    loc = LOCATOR_RE.search(text)
    if loc:
        ref.year = int(loc.group("year"))
        ref.volume = (loc.group("vol") or "").strip() or None
        ref.issue = (loc.group("issue") or "").strip() or None
        ref.first_page = loc.group("fp")
        ref.last_page = expand_page(loc.group("fp"), loc.group("lp"))

    segments = _split_segments(text)
    if not segments:
        return ref

    # --- author segment ------------------------------------------------------
    ref.authors, ref.corporate = _parse_authors(segments[0])
    body = segments[1:]

    # --- book chapter: "In: <editors>, eds. <book title>[, Nth edn]. <imprint>"
    for i, seg in enumerate(body):
        if CHAPTER_IN_RE.match(seg.rstrip(".") + "."):
            ref.is_book = ref.is_chapter = True
            ref.title = " ".join(body[:i]).rstrip(".").strip()
            rest = body[i + 1:]
            if rest:
                bt = rest[0].rstrip(".").strip()
                em = INLINE_EDITION_RE.search(bt)
                if em:
                    ref.edition = em.group("ed")
                    bt = INLINE_EDITION_RE.sub("", bt).strip()
                ref.book_title = bt
            for seg2 in rest[1:]:
                bm = BOOK_TAIL_RE.match(seg2.strip())
                if bm:
                    ref.place = bm.group("place").strip()
                    ref.publisher = bm.group("publisher").strip()
                    ref.year = int(bm.group("year"))
                    ref.first_page = bm.group("fp")
                    ref.last_page = expand_page(bm.group("fp"), bm.group("lp"))
                    break
            ref.title = re.sub(r"\s+", " ", ref.title).strip(" .,;")
            return ref

    # --- edition marker ------------------------------------------------------
    for i, seg in enumerate(list(body)):
        em = EDITION_RE.match(seg.rstrip("."))
        if em:
            ref.edition = em.group("ed")
            body.pop(i)
            break

    # --- locate the tail: either a locator segment or a book imprint ---------
    tail_idx = None
    for i, seg in enumerate(body):
        if LOCATOR_RE.search(seg):
            tail_idx = i
            break
        bm = BOOK_TAIL_RE.match(seg)
        if bm:
            ref.is_book = True
            ref.place = bm.group("place").strip()
            ref.publisher = bm.group("publisher").strip()
            ref.year = ref.year or int(bm.group("year"))
            if bm.group("fp"):
                ref.first_page = ref.first_page or bm.group("fp")
                ref.last_page = ref.last_page or expand_page(bm.group("fp"), bm.group("lp"))
            tail_idx = i
            break

    if tail_idx is None:
        # No locator and no imprint: fall back to the last segment that is just
        # a year (e.g. "Lancet. 2024.") — then journal is the segment before it.
        for i in range(len(body) - 1, -1, -1):
            if re.fullmatch(r"(?:19|20)\d{2}\.?", body[i].strip()):
                tail_idx = i
                ref.year = ref.year or int(body[i].strip().rstrip("."))
                break

    if tail_idx is None:
        # last resort: title is everything, year from anywhere in the string
        ref.title = " ".join(body).rstrip(".").strip()
    elif ref.is_book:
        ref.title = " ".join(body[:tail_idx]).rstrip(".").strip()
    else:
        # segment immediately before the tail is the journal, unless the tail
        # segment itself carries the journal ("Lancet 2017;390(1):1-2")
        jrn_idx = tail_idx - 1
        tail_seg = body[tail_idx]
        pre_locator = tail_seg[: LOCATOR_RE.search(tail_seg).start()].strip(" ,.;") if LOCATOR_RE.search(tail_seg) else ""
        if pre_locator and not re.fullmatch(r"(?:19|20)\d{2}", pre_locator):
            ref.journal = pre_locator
            ref.title = " ".join(body[:tail_idx]).rstrip(".").strip()
        elif jrn_idx >= 0:
            ref.journal = body[jrn_idx].rstrip(".").strip()
            ref.title = " ".join(body[:jrn_idx]).rstrip(".").strip()
        else:
            ref.title = " ".join(body[:tail_idx]).rstrip(".").strip()

    if ref.year is None:
        years = YEAR_RE.findall(text)
        if years:
            ref.year = int(re.findall(r"\b(?:19|20)\d{2}\b", text)[-1])

    # a book with no imprint but an edition marker is still a book
    if not ref.is_book and ref.publisher is None and ref.volume is None and ref.edition:
        ref.is_book = True

    ref.title = re.sub(r"\s+", " ", ref.title).strip(" .,;")
    ref.journal = re.sub(r"\s+", " ", ref.journal).strip(" .,;")
    return ref


def parse_all(refs: dict[int, str]) -> dict[int, ParsedRef]:
    return {n: parse_reference(n, txt) for n, txt in refs.items()}

"""Bibliography extraction from .docx and citation-marker rewriting.

Marker logic is carried over from the original script — it worked. The change is
what gets substituted: only references that passed the accept gate become live
Scannable Cite markers. Anything else stays visibly unresolved in the document
rather than silently pointing at the wrong paper.
"""
from __future__ import annotations

import re

HEADERS = re.compile(r"^\s*(references|bibliography|works cited|reference list)\s*:?\s*$", re.I)
# Superscript citation notation. Beyond the digits, journal templates in the
# Lancet family use U+00B7 MIDDLE DOT as the separator between citations and
# U+207B SUPERSCRIPT MINUS for ranges, so "¹·²⁻⁴" means refs 1, 2-4.
#
# The same middle dot is that house style's decimal separator ("39·1%"), which is
# why SUPRUN must begin and end with a superscript digit: a decimal point sits
# between ASCII digits and therefore cannot match.
SUP_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
SUP_SEPS = "⁻·"
SUP = str.maketrans(SUP_DIGITS + SUP_SEPS, "0123456789" + "-,")
SUPRUN = re.compile(f"[{SUP_DIGITS}](?:[{SUP_DIGITS}{SUP_SEPS}]*[{SUP_DIGITS}])?")
GROUP = re.compile(r"[\[(]\s*([\d\s,\-–—]+?)\s*[\])]")

# Digits fused directly onto sentence punctuation with no space:
#   "...better-resourced systems.29-32"   "...differing rates.41-42"
# This is what a superscript citation degrades into when the formatting is lost
# (or stripped while hand-editing), and it is otherwise unambiguous: ordinary
# prose puts a space after a full stop.
#
# The lookbehind demands a *letter* before the punctuation, which is what keeps
# decimals out — in "p=0.05" the character before the stop is a digit. Any
# survivor that isn't a real reference number is dropped later by expand().
PLAIN_ATTACHED = re.compile(
    r"(?<=[A-Za-z][.;,!?])"
    r"(\d{1,3}(?:\s*[-–—]\s*\d{1,3})?(?:\s*,\s*\d{1,3}(?:\s*[-–—]\s*\d{1,3})?)*)"
)
# Entry numbering, e.g. "1. Smith J", "[1] Smith J", "1) Smith J", and — as
# produced by Lancet-family templates — "1<em-space>Smith J" with no delimiter at
# all. The delimiter is therefore optional, but separating whitespace is not:
# without that requirement a title beginning with a digit would be truncated.
LEAD = re.compile(
    r"^\s*[\[(]?(\d{1,3})[\])\.]?"
    r"[\s -   　 ]+(.*)$"
)

# Unicode spaces that must be folded to ASCII before any of the above matches.
WEIRD_SPACE = re.compile(r"[ -   　 ]")
STOP_HEADINGS = re.compile(r"^\s*(tables?|figures?|appendix|supplement\w*)\s*:?\s*$", re.I)

# Widest plausible citation range. Beyond this, treat it as a typo, not a range.
MAX_RANGE_SPAN = 8


def find_biblio_index(doc) -> int | None:
    for i, p in enumerate(doc.paragraphs):
        if HEADERS.match(p.text):
            return i
    return None


def parse_bibliography(text: str) -> dict[int, str]:
    """Split a bibliography block into {number: reference text}.

    Exotic Unicode spaces are folded to ASCII first. Journal templates in the
    Lancet family separate the entry number from the author with an em space
    (U+2003) and no delimiter; left unfolded, every entry fails to match, the
    numbering fallback kicks in, and the digits stay glued to the first author's
    surname — which silently corrupts author matching for the whole document.
    """
    text = WEIRD_SPACE.sub(" ", text)
    entries: dict[int, str] = {}
    cur: list[str] = []
    num: int | None = None
    for line in text.splitlines():
        if STOP_HEADINGS.match(line):
            break
        m = LEAD.match(line)
        if m:
            if num is not None:
                entries[num] = " ".join(cur).strip()
            num, cur = int(m.group(1)), [m.group(2)]
        elif line.strip() and num is not None:
            cur.append(line.strip())
    if num is not None:
        entries[num] = " ".join(cur).strip()
    if not entries:
        # Unnumbered list: take paragraph order, but still stop at a trailing
        # Tables/Figures section rather than importing its captions as references.
        i = 0
        for line in text.splitlines():
            if STOP_HEADINGS.match(line):
                break
            if line.strip():
                i += 1
                entries[i] = line.strip()
    return entries


def make_renderer(
    resolutions: dict,
    keys: dict[int, str],
    uid: str,
    style: str = "scannable",
    refs: dict | None = None,
    warnings: list[str] | None = None,
):
    """Return render(spec) -> replacement text for a citation group like '1,3-5'.

    Oversized ranges are refused rather than expanded. A superscript range
    spanning dozens of references is a dropped digit in the manuscript
    ("\\u00b3\\u207b\\u00b3\\u00b2" where "\\u00b3\\u00b9\\u207b\\u00b3\\u00b2" was meant), and expanding it would
    manufacture 30 citations the author never made. Those are collected in
    `warnings` for the caller to report, and the document text is left untouched.
    """

    def expand(spec: str) -> list[int]:
        out: list[int] = []
        for chunk in spec.replace("–", "-").replace("—", "-").split(","):
            chunk = chunk.strip()
            if "-" in chunk:
                a, b = chunk.split("-", 1)
                if a.strip().isdigit() and b.strip().isdigit():
                    lo, hi = int(a), int(b)
                    if lo not in resolutions and hi not in resolutions:
                        # Neither endpoint is a reference number, so this is
                        # ordinary prose — a year span like "(1990-2023)" or a
                        # value range. Not a citation, and not worth a warning.
                        return []
                    if hi - lo > MAX_RANGE_SPAN:
                        if warnings is not None:
                            warnings.append(
                                f"range '{lo}-{hi}' spans {hi - lo + 1} references — "
                                "likely a dropped digit in the manuscript; left unchanged"
                            )
                        return []
                    out += range(lo, hi + 1)
            elif chunk.isdigit():
                out.append(int(chunk))
        return [n for n in out if n in resolutions]

    def label(n: int) -> str:
        """Human-readable half of the marker. Zotero renders the real citation
        from the stored item, so this only has to be legible — but take the
        surname from the manuscript, which preserves casing the matcher folded
        away ("McMichael", not "Mcmichael")."""
        c = resolutions[n].candidate
        if not c:
            return f"ref {n}"
        ref = (refs or {}).get(n)
        who = (
            (ref.corporate if ref else None)
            or c.corporate
            or (ref.authors[0] if ref and ref.authors else None)
            or (c.authors[0].title() if c.authors else "Anon")
        )
        return f"{who}, ({c.year or 'n.d.'})"

    def render(spec: str) -> str | None:
        nums = expand(spec)
        if not nums:
            return None
        parts = []
        for n in nums:
            res = resolutions[n]
            if res.status in ("ACCEPTED", "FROM_TEXT") and keys.get(n):
                if style == "scannable":
                    parts.append(f"{{ | {label(n)} | | |zu:{uid}:{keys[n]}}}")
                else:
                    parts.append(f"{{{label(n)}}}")
            else:
                parts.append(f"{{NEEDS REVIEW: ref {n}}}")
        return "".join(parts)

    return render


def _is_math_superscript(text: str, start: int, end: int) -> bool:
    """True when a superscript run is mathematics, not a citation.

    Scientific manuscripts are full of superscripts that must never become
    citations. Writing a Zotero marker into the middle of a number would corrupt
    the results section:

        -13.3x10^-3   the exponent is not reference 3
        R^2=0.32      the square is not reference 2
        km^2          likewise

    Citations, by contrast, follow a word or clause punctuation. Three rejections
    cover the cases seen in practice.
    """
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""

    # 1. exponent attached to a number: "10^-3", "2^8"
    if before.isdigit():
        return True
    # 1b. the run begins part-way into a superscript expression. SUPRUN must start
    #     on a digit, so "10^-3" yields a match on the "3" alone, preceded by the
    #     superscript minus. A citation never starts mid-superscript.
    if before in SUP_SEPS or before == "×":
        return True
    # 2. an assignment follows: "R^2=0.32"
    if after == "=":
        return True
    # 3. a lone-letter variable precedes: "R^2", "n^2" — but not "et al^20",
    #    where the preceding token is a real word.
    if before.isalpha():
        token_start = start - 1
        while token_start > 0 and text[token_start - 1].isalpha():
            token_start -= 1
        if start - token_start == 1:
            return True
    return False


def _run_spans(p) -> list[tuple[int, int, int]]:
    """[(run_index, start, end)] in paragraph-text coordinates."""
    spans, pos = [], 0
    for i, r in enumerate(p.runs):
        spans.append((i, pos, pos + len(r.text)))
        pos += len(r.text)
    return spans


def _citation_spans(p) -> list[tuple[int, int, str]]:
    """Locate every citation marker in a paragraph as (start, end, number_spec).

    Works in paragraph-text coordinates rather than per-run, because Word freely
    splits a single citation across runs — "statement;²" + "⁰" is one citation,
    and no per-run scan can see it. Three notations are recognised:

      * bracketed groups          [1]  (2,3)  [4-6]
      * Unicode superscript chars ²³  ⁴²⁻⁴⁸
      * Word superscript runs     digits carrying superscript formatting
    """
    text = p.text
    found: list[tuple[int, int, str]] = []

    for m in GROUP.finditer(text):
        found.append((m.start(), m.end(), m.group(1)))
    for m in SUPRUN.finditer(text):
        if _is_math_superscript(text, m.start(), m.end()):
            continue
        found.append((m.start(), m.end(), m.group(0).translate(SUP)))
    for m in PLAIN_ATTACHED.finditer(text):
        found.append((m.start(1), m.end(1), m.group(1)))

    # Contiguous Word-superscript formatting, merged across run boundaries.
    runs = p.runs
    start = None
    for i, s, e in _run_spans(p):
        is_sup = bool(runs[i].font.superscript) and bool(runs[i].text.strip())
        if is_sup and start is None:
            start = s
        elif not is_sup and start is not None:
            found.append((start, s, text[start:s]))
            start = None
    if start is not None:
        found.append((start, len(text), text[start:]))

    # Drop overlaps, preferring the earliest and longest match.
    found.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    out: list[tuple[int, int, str]] = []
    last_end = -1
    for s, e, spec in found:
        if s < last_end or not re.search(r"\d", spec):
            continue
        out.append((s, e, spec))
        last_end = e
    return out


def _apply_spans(p, replacements: list[tuple[int, int, str]]) -> None:
    """Replace paragraph-coordinate spans, distributing edits across runs.

    Applied right-to-left so that earlier spans' offsets stay valid, which is why
    the run map is computed once from the original text and never refreshed.
    """
    runs = p.runs
    run_map = _run_spans(p)
    for s, e, rep in sorted(replacements, key=lambda t: -t[0]):
        first = True
        edits: dict[int, str] = {}
        for i, rs, re_ in run_map:
            if re_ <= s or rs >= e:
                continue
            txt = runs[i].text
            local_s, local_e = max(s, rs) - rs, min(e, re_) - rs
            if first:
                edits[i] = txt[:local_s] + rep + txt[local_e:]
                first = False
            else:
                edits[i] = txt[:local_s] + txt[local_e:]
        for i, t in edits.items():
            runs[i].text = t
            # the marker is body text; superscripting it would break ODF Scan
            runs[i].font.superscript = False


def mark_body(doc, biblio_idx: int, render) -> int:
    """Rewrite in-text citations before the bibliography. Returns markers written."""
    count = 0
    for p in doc.paragraphs[:biblio_idx]:
        if not p.text.strip():
            continue
        replacements = []
        for s, e, spec in _citation_spans(p):
            rep = render(spec)
            if rep:
                replacements.append((s, e, rep))
        if replacements:
            _apply_spans(p, replacements)
            count += len(replacements)
    return count


def remove_from(doc, idx: int) -> None:
    for p in list(doc.paragraphs)[idx:]:
        p._element.getparent().remove(p._element)

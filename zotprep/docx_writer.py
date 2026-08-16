"""Bibliography extraction from .docx and citation-marker rewriting.

Marker logic is carried over from the original script — it worked. The change is
what gets substituted: only references that passed the accept gate become live
citations. Anything else stays visibly unresolved in the document rather than
silently pointing at the wrong paper.

Two output styles, and they differ in more than formatting:

  fields      Word field codes Zotero reads directly (`ADDIN ZOTERO_ITEM`). The
              document is finished when it is downloaded.
  scannable   Scannable Cite markers for the Zotero ODF Scan plugin, which
              converts them in a second pass. Kept as a fallback.
"""
from __future__ import annotations

import copy
import json
import random
import re
import string

from lxml import etree

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

# The same degradation, but with the space surviving:
#
#     "...in adults aged 45 years and older. 1 According to..."
#     "...the disease burden being for knee OA 2."
#     "...short-lived analgesia 6,7."
#
# This is what a superscript citation becomes when a manuscript is pasted as
# plain text, and it is the one notation that is genuinely ambiguous with prose —
# "25 patients", "Group 4", "for 12 months" have exactly the same shape. So the
# match is deliberately conservative, and every guard below exists because a
# real manuscript in the corpus tripped over it. A missed citation leaves a
# visible "{NEEDS REVIEW}"; a false one rewrites the author's sentence.
_NUMS = r"\d{1,3}(?:\s*[-–—]\s*\d{1,3})?(?:\s*,\s*\d{1,3}(?:\s*[-–—]\s*\d{1,3})?)*"
PLAIN_DETACHED = re.compile(
    rf"(?P<word>[A-Za-z]+)(?P<punct>[.;,!?])?[  ](?P<nums>{_NUMS})(?P<post>.|$)"
)

# Words that take a number as their *label* ("Group 4", "Table 2"), and function
# words a citation never follows ("of 25", "than 12"). Either way the digits are
# prose, not a reference.
DETACHED_BLOCK = frozenset({
    "group", "groups", "grade", "grades", "table", "tables", "figure", "figures", "fig",
    "stage", "phase", "type", "level", "class", "no", "number", "chapter", "part",
    "section", "step", "arm", "visit", "score", "kl", "n", "p", "r",
    "version", "item", "question", "page", "line",
    "of", "to", "in", "at", "for", "from", "by", "with", "than", "up", "over", "under",
    "about", "approximately", "and", "or", "was", "were", "is", "are", "be", "been",
    "into", "onto", "per", "versus", "vs", "until", "till", "between", "within",
    "mean", "median", "total", "only", "all", "aged", "age", "range", "ratio",
    "the", "a", "an", "had", "has", "have", "each", "both", "every", "another",
})

# Timepoints cut both ways: "By month 1," labels the timepoint, while "beyond
# 3 months 6,8." is a unit that already took its number — what follows it is a
# citation. The digit before the word is what separates them.
DETACHED_TIME = frozenset({
    "month", "months", "week", "weeks", "day", "days", "year", "years",
    "hour", "hours", "visit", "visits", "session", "sessions", "cycle", "cycles",
})
COUNTED_BEFORE = re.compile(r"\d\s*$")

# A measurement the digits belong to rather than a citation: "1, 3, 6 and 12
# months", "4-6 times baseline", "37 studies comprising 4,326 patients".
DETACHED_UNITS = frozenset({
    "month", "months", "week", "weeks", "day", "days", "year", "years", "hour", "hours",
    "minute", "minutes", "time", "times", "fold", "patient", "patients", "case", "cases",
    "subject", "subjects", "participant", "participants", "study", "studies", "trial",
    "trials", "ml", "mm", "cm", "mg", "kg", "g", "l", "percent", "point", "points",
    "degree", "degrees", "unit", "units", "group", "groups", "session", "sessions",
    "injection", "injections", "site", "sites", "million", "billion", "thousand",
})
TRAILING_UNIT = re.compile(r"^[\s)]*(?:and\s+\d{1,3}\s+)?([A-Za-z]+)")
THOUSANDS = re.compile(r"^\d{1,3},\d{3}$")
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

# A figure or table caption, which ends the bibliography as surely as a heading
# does. Trailing matter often has no heading at all — the legends simply begin —
# so the caption is the only marker that the reference list has stopped. Tried
# only after LEAD has failed, so a numbered reference whose title happens to
# start "Table ..." is still read as a reference.
CAPTION = re.compile(
    r"^\s*(?:supplementary|supplemental|appendix|online)?\s*"
    r"(?:figures?|figs?|tables?|charts?|box(?:es)?|schemes?|panels?|exhibits?)"
    r"\s*\.?\s*(?:\d|[SE]\d|[IVX]+[\.\s:]|[A-Z][\.\s:])",
    re.I,
)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

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
    new_id=None,
    items: dict | None = None,
):
    """Return render(spec) -> replacement for a citation group like '1,3-5'.

    With `style="fields"` the replacement is a list of pieces for
    `mark_body_fields`; otherwise it is the marker text `mark_body` writes.

    Oversized ranges are refused rather than expanded — but only in the notation
    where they are a mistake. A superscript range spanning dozens of references
    is a dropped digit ("\\u00b3\\u207b\\u00b3\\u00b2" where "\\u00b3\\u00b9\\u207b\\u00b3\\u00b2" was meant), because that
    is the notation a leading digit goes missing from. Brackets are typed
    deliberately, and a systematic review genuinely cites its included studies
    as "[14-49]", so a wide bracketed range is taken as written and reported
    rather than refused. Both kinds of note go to `warnings` for the caller.
    """

    def expand(spec: str, bracketed: bool = False) -> list[int]:
        out: list[int] = []
        for chunk in spec.replace("–", "-").replace("—", "-").split(","):
            chunk = chunk.strip()
            if "-" in chunk:
                a, b = chunk.split("-", 1)
                if a.strip().isdigit() and b.strip().isdigit():
                    lo, hi = int(a), int(b)
                    # No reference is numbered 0, so a range starting there is a
                    # measurement scale: "VAS (0-10)", "KOOS total (0-100)". The
                    # wide ones are refused as implausible ranges below, but a
                    # narrow "(0-5)" would otherwise expand into five citations
                    # the author never wrote.
                    if lo == 0:
                        return []
                    if lo not in resolutions and hi not in resolutions:
                        # Neither endpoint is a reference number, so this is
                        # ordinary prose — a year span like "(1990-2023)" or a
                        # value range. Not a citation, and not worth a warning.
                        return []
                    if hi - lo > MAX_RANGE_SPAN and not bracketed:
                        # A wide *superscript* range is a dropped digit: the
                        # notation is exactly where a leading digit goes missing
                        # when formatting is lost, and expanding it would
                        # manufacture citations the author never made.
                        if warnings is not None:
                            warnings.append(
                                f"range '{lo}-{hi}' spans {hi - lo + 1} references — "
                                "likely a dropped digit in the manuscript; left unchanged"
                            )
                        return []
                    if hi - lo > MAX_RANGE_SPAN and warnings is not None:
                        # Brackets are typed deliberately, and a systematic
                        # review really does cite its included studies this way:
                        # "included in both syntheses [14-49]". Taken as written,
                        # but reported, because it is a lot of citations to make
                        # from one marker.
                        warnings.append(
                            f"range '{lo}-{hi}' expands to {hi - lo + 1} references — "
                            "bracketed, so taken as written; check it is not a typo"
                        )
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

    def render(spec: str, bracketed: bool = False) -> str | None:
        nums = expand(spec, bracketed)
        if not nums:
            return None
        parts = []
        for n in nums:
            res = resolutions[n]
            # Deleted at review: the reference contributes nothing. When every
            # reference in the group was deleted the result is "", which is a
            # deletion — distinct from None, which leaves the text alone.
            if res.status == "DROPPED":
                continue
            if res.status in ("ACCEPTED", "FROM_TEXT") and keys.get(n):
                if style == "scannable":
                    parts.append(f"{{ | {label(n)} | | |zu:{uid}:{keys[n]}}}")
                else:
                    parts.append(f"{{{label(n)}}}")
            else:
                parts.append(f"{{NEEDS REVIEW: ref {n}}}")
        return "".join(parts)

    def render_fields(spec: str, bracketed: bool = False):
        """One citation becomes one field however many references it carries.

        That is how Zotero models "6,7" — a single citation of two items.
        Anything unresolved stays visible text between the fields rather than
        being folded into one, so a half-resolved group cannot look resolved.
        """
        nums = expand(spec, bracketed)
        if not nums:
            return None
        pieces: list[dict] = []
        cited: list[dict] = []

        def flush() -> None:
            if not cited:
                return
            pieces.append({
                "kind": "field",
                "json": citation_json(cited, uid, make_id()),
                "label": "; ".join(i["label"] for i in cited),
            })
            cited.clear()

        for n in nums:
            res = resolutions[n]
            if res.status == "DROPPED":
                continue
            if res.status in ("ACCEPTED", "FROM_TEXT") and keys.get(n):
                cited.append({"key": keys[n], "label": label(n),
                              "item": (items or {}).get(n)})
            else:
                flush()
                pieces.append({"kind": "text", "value": f"{{NEEDS REVIEW: ref {n}}}"})
        flush()
        # An empty list is a deletion — every reference in the group was deleted
        # at review. None, by contrast, leaves the text alone.
        return pieces

    make_id = new_id or random_citation_id
    return render_fields if style == "fields" else render


# --- Zotero fields ------------------------------------------------------------
#
# A live Zotero citation in a .docx is a Word field, five runs long:
#
#   fldChar begin | instrText " ADDIN ZOTERO_ITEM CSL_CITATION {json} "
#                 | fldChar separate | the visible text | fldChar end
#
# The JSON shape below is copied from a document Zotero itself produced — the
# same keys, in the same order, with no additions. Zotero matches items by the
# `uris` entry; everything else is what it shows before the first refresh.
#
# Writing these directly is what removes ODF Scan from the workflow. ODF Scan
# finds its markers by scanning document.xml as a string, which is why a picture
# whose XML happens to contain `uri="{...}"` can end up with a citation spliced
# into the middle of an attribute, producing a file Word refuses to open.
CSL_SCHEMA = "https://github.com/citation-style-language/schema/raw/master/csl-citation.json"
ID_ALPHABET = string.ascii_letters + string.digits
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def random_citation_id(rng: random.Random | None = None) -> str:
    """Zotero's citationID: eight characters, unique within the document."""
    r = rng or random
    return "".join(r.choice(ID_ALPHABET) for _ in range(8))


CSL_TYPE = {"journalArticle": "article-journal", "book": "book", "bookSection": "chapter"}


def csl_item_data(item: dict, key: str) -> dict:
    """A Zotero item dict as CSL-JSON, for embedding in the field.

    This is what makes the document portable. A citation carries a `uris` entry
    pointing into the library it came from; on any other machine that URI
    resolves to nothing, and Zotero asks the reader to pick a substitute item —
    which is what a co-author sees if the field says nothing else. Zotero's own
    plugin therefore embeds the whole record alongside the URI, and so does this.
    """
    data: dict = {
        "id": key,
        "type": CSL_TYPE.get(item.get("itemType", ""), "article-journal"),
        "title": item.get("title") or "",
    }
    container = item.get("publicationTitle") or item.get("bookTitle")
    if container:
        data["container-title"] = container
    if item.get("journalAbbreviation"):
        data["journalAbbreviation"] = item["journalAbbreviation"]
    for src, dst in (("volume", "volume"), ("issue", "issue"), ("pages", "page"),
                     ("DOI", "DOI"), ("edition", "edition"), ("publisher", "publisher"),
                     ("place", "publisher-place")):
        if item.get(src):
            data[dst] = item[src]

    authors = []
    for c in item.get("creators") or []:
        if c.get("name"):
            authors.append({"literal": c["name"]})
        elif c.get("lastName"):
            entry = {"family": c["lastName"]}
            if c.get("firstName"):
                entry["given"] = c["firstName"]
            authors.append(entry)
    if authors:
        data["author"] = authors

    # Only ever a year here, which is a legal date-parts of length one.
    year = str(item.get("date") or "").strip()
    if year:
        data["issued"] = {"date-parts": [[year]]}
    return data


def citation_json(items: list[dict], uid: str, cid: str) -> str:
    """The CSL_CITATION payload for one citation, which may carry several items.

    Serialised without spaces after the separators, matching what Zotero writes.
    """
    shown = "; ".join(i["label"] for i in items)
    cited = []
    for i in items:
        entry: dict = {
            "id": i["key"],
            "uris": [f"http://zotero.org/users/{uid}/items/{i['key']}"],
        }
        if i.get("item"):
            entry["itemData"] = csl_item_data(i["item"], i["key"])
        cited.append(entry)
    return json.dumps(
        {
            "citationID": cid,
            "properties": {
                "formattedCitation": shown,
                "plainCitation": shown,
                "noteIndex": 0,
            },
            "citationItems": cited,
            "schema": CSL_SCHEMA,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _fld_char_run(el_factory, kind: str):
    r = el_factory(W + "r")
    fc = el_factory(W + "fldChar")
    fc.set(W + "fldCharType", kind)
    r.append(fc)
    return r


def _field_runs(el_factory, json_text: str, label: str, rpr):
    """The five runs of one Zotero field.

    `rpr` is the formatting of the text being replaced, so the citation reads in
    the manuscript's font rather than Word's default.
    """
    runs = [_fld_char_run(el_factory, "begin")]

    instr_run = el_factory(W + "r")
    instr = el_factory(W + "instrText")
    instr.set(XML_SPACE, "preserve")
    # lxml escapes for us, which is the whole point: the JSON is data, and a
    # title containing "<" must not become markup.
    instr.text = f" ADDIN ZOTERO_ITEM CSL_CITATION {json_text} "
    instr_run.append(instr)
    runs += [instr_run, _fld_char_run(el_factory, "separate")]

    shown = el_factory(W + "r")
    if rpr is not None:
        shown.append(copy.deepcopy(rpr))
    t = el_factory(W + "t")
    t.set(XML_SPACE, "preserve")
    t.text = label
    shown.append(t)
    runs += [shown, _fld_char_run(el_factory, "end")]

    return runs


def _text_run(el_factory, value: str, rpr):
    """A plain-text run in the surrounding formatting, for anything unresolved."""
    r = el_factory(W + "r")
    # copied, never moved: the source run is still in the paragraph and still
    # owns its own formatting
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    t = el_factory(W + "t")
    t.set(XML_SPACE, "preserve")
    t.text = value
    r.append(t)
    return r


def _baseline_rpr(run):
    """The run's formatting, with superscript stripped.

    The superscript belongs to the notation being replaced, not to the citation
    replacing it — and a superscripted field reads as a footnote marker.
    """
    rpr = run._r.find(W + "rPr")
    if rpr is None:
        return None
    copied = copy.deepcopy(rpr)
    for va in copied.findall(W + "vertAlign"):
        copied.remove(va)
    return copied


def _apply_pieces(p, replacements: list[tuple[int, int, list[dict]]]) -> None:
    """Replace citation spans with runs, rather than with text.

    The text path can edit a run's characters in place; a field cannot, because
    it *is* several runs. So the run holding the marker is split — the prefix
    stays where it was, the field runs are inserted after it, and any tail
    becomes a new run carrying the same formatting.

    Right-to-left for the same reason _apply_spans is: the run map is computed
    once from the original text, and editing from the end keeps earlier offsets
    valid.
    """
    runs = p.runs
    run_map = _run_spans(p)
    el_factory = etree.Element

    for s, e, pieces in sorted(replacements, key=lambda t: -t[0]):
        touched = [(i, rs, re_) for i, rs, re_ in run_map if not (re_ <= s or rs >= e)]
        if not touched:
            continue

        first_i, first_start, _ = touched[0]
        first_run = runs[first_i]
        rpr = _baseline_rpr(first_run)
        last_i, last_start, last_end = touched[-1]
        last_run = runs[last_i]
        tail = last_run.text[min(e, last_end) - last_start:]
        tail_rpr = last_run._r.find(W + "rPr")
        tail_rpr = None if tail_rpr is None else copy.deepcopy(tail_rpr)

        # Everything the span covers goes; the prefix of the first run stays.
        first_run.text = first_run.text[:max(s, first_start) - first_start]
        for i, _, _ in touched[1:]:
            runs[i].text = ""

        new_nodes = []
        for piece in pieces:
            if piece["kind"] == "field":
                new_nodes += _field_runs(el_factory, piece["json"], piece["label"], rpr)
            else:
                new_nodes.append(_text_run(el_factory, piece["value"], rpr))
        if tail:
            new_nodes.append(_text_run(el_factory, tail, tail_rpr))

        parent = first_run._r.getparent()
        at = parent.index(first_run._r) + 1
        for offset, node in enumerate(new_nodes):
            parent.insert(at + offset, node)


def mark_body_fields(doc, biblio_idx: int, render) -> int:
    """Rewrite in-text citations as live Zotero fields. Returns fields written.

    Takes a renderer built with `style="fields"`, which returns a list of pieces
    rather than a string.
    """
    count = 0
    for p in doc.paragraphs[:biblio_idx]:
        if not p.text.strip():
            continue
        text = p.text
        replacements = []
        for s, e, spec, bracketed in _citation_spans(p):
            pieces = render(spec, bracketed)
            if pieces == []:
                # every reference in this citation was deleted at review
                replacements.append((*deletion_span(text, s, e), []))
            elif pieces:
                replacements.append((s, e, pieces))
        if replacements:
            _apply_pieces(p, replacements)
            count += len(replacements)
    return count


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


def _detached_spans(text: str) -> list[tuple[int, int, str]]:
    """Bare digits separated from the preceding word by a space, as citations.

    Every rejection below is a guard against prose, in the order it is cheapest
    to test. See PLAIN_DETACHED for why they are all needed.
    """
    out: list[tuple[int, int, str]] = []
    for m in PLAIN_DETACHED.finditer(text):
        word, punct, nums = m.group("word"), m.group("punct"), m.group("nums")
        post = m.group("post")
        low = word.lower()
        if low in DETACHED_BLOCK:
            continue
        if low in DETACHED_TIME and not COUNTED_BEFORE.search(text[:m.start("word")]):
            continue
        # "10 mL", "0-100%", "4-6 times" — the digits are being measured.
        if post and (post.isdigit() or post in "%/×−-–—"):
            continue
        if THOUSANDS.match(nums):
            continue
        tail = TRAILING_UNIT.match(text[m.end("nums"):])
        if tail and tail.group(1).lower() in DETACHED_UNITS:
            continue
        vals = [int(v) for v in re.findall(r"\d+", nums)]
        # No reference is numbered 0, and citation lists run upwards without
        # repeating — "2, 3, 1 and 4 losses to follow-up" does neither.
        if any(v == 0 for v in vals) or sorted(set(vals)) != vals:
            continue
        # "(baseline 62.14 ± 5.24" — the stop is a decimal point.
        if post == "." and text[m.end("nums") + 1:m.end("nums") + 2].isdigit():
            continue
        ends_clause = post == "" or post in ".,;:!?"
        # A bare number mid-sentence is prose ("25 patients were assigned"). It
        # takes an ended sentence before it, an ended clause after it, or the
        # comma-separated shape prose does not use.
        if not ((punct in ".!?" if punct else False) or ends_clause or len(vals) > 1):
            continue
        out.append((m.start("nums"), m.end("nums"), nums))
    return out


def _citation_spans(p) -> list[tuple[int, int, str, bool]]:
    """Locate every citation marker as (start, end, number_spec, bracketed).

    Works in paragraph-text coordinates rather than per-run, because Word freely
    splits a single citation across runs — "statement;²" + "⁰" is one citation,
    and no per-run scan can see it. Four notations are recognised:

      * bracketed groups          [1]  (2,3)  [4-6]
      * Unicode superscript chars ²³  ⁴²⁻⁴⁸
      * Word superscript runs     digits carrying superscript formatting
      * plain digits              fused to punctuation, or space-separated

    `bracketed` travels with the span because it decides how much to trust a
    wide range: see the note on MAX_RANGE_SPAN in make_renderer.
    """
    text = p.text
    found: list[tuple[int, int, str, bool]] = []

    for m in GROUP.finditer(text):
        found.append((m.start(), m.end(), m.group(1), True))
    for m in SUPRUN.finditer(text):
        if _is_math_superscript(text, m.start(), m.end()):
            continue
        found.append((m.start(), m.end(), m.group(0).translate(SUP), False))
    for m in PLAIN_ATTACHED.finditer(text):
        found.append((m.start(1), m.end(1), m.group(1), False))
    found += [(s, e, spec, False) for s, e, spec in _detached_spans(text)]

    # Contiguous Word-superscript formatting, merged across run boundaries.
    runs = p.runs
    start = None
    for i, s, e in _run_spans(p):
        is_sup = bool(runs[i].font.superscript) and bool(runs[i].text.strip())
        if is_sup and start is None:
            start = s
        elif not is_sup and start is not None:
            found.append((start, s, text[start:s], False))
            start = None
    if start is not None:
        found.append((start, len(text), text[start:], False))

    # Drop overlaps, preferring the earliest and longest match.
    found.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    out: list[tuple[int, int, str, bool]] = []
    last_end = -1
    for s, e, spec, bracketed in found:
        if s < last_end or not re.search(r"\d", spec):
            continue
        out.append((s, e, spec, bracketed))
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


def deletion_span(text: str, s: int, e: int) -> tuple[int, int]:
    """Widen a span whose replacement is empty, to take its spacing with it.

    A citation marker is written against the word before it — "knee OA 2." —
    so removing just the digits leaves "knee OA ." or a double space. The space
    in front belongs to the marker whenever what follows is punctuation, another
    space, or the end of the paragraph.
    """
    after = text[e] if e < len(text) else ""
    if s > 0 and text[s - 1] == " " and (after == "" or after == " " or after in ".,;:!?)]"):
        return s - 1, e
    return s, e


def mark_body(doc, biblio_idx: int, render) -> int:
    """Rewrite in-text citations before the bibliography. Returns markers written."""
    count = 0
    for p in doc.paragraphs[:biblio_idx]:
        if not p.text.strip():
            continue
        text = p.text
        replacements = []
        for s, e, spec, bracketed in _citation_spans(p):
            rep = render(spec, bracketed)
            if rep == "":
                # every reference in this citation was deleted at review
                replacements.append((*deletion_span(text, s, e), ""))
            elif rep:
                replacements.append((s, e, rep))
        if replacements:
            _apply_spans(p, replacements)
            count += len(replacements)
    return count


def count_citations(doc, biblio_idx: int) -> int:
    """How many citation markers the body holds, without rewriting anything.

    Asked before any resolution work, because zero is not a result worth several
    minutes of searching — it means the document's citation notation was not
    recognised, and continuing would delete a bibliography and put nothing in
    its place.
    """
    return sum(len(_citation_spans(p)) for p in doc.paragraphs[:biblio_idx] if p.text.strip())


def _has_image(el) -> bool:
    return any(next(el.iter(f"{W}{tag}"), None) is not None
               for tag in ("drawing", "pict", "object"))


def biblio_end_index(doc, biblio_idx: int) -> int:
    """Paragraph index one past the end of the bibliography block.

    A manuscript does not end at its reference list. Figure legends and the
    images they caption, table captions, the tables themselves and appendices
    all come after it, and deleting to the end of the document takes every one
    of them. Two of those losses are worse than they look:

      * Word merges two tables that end up as adjacent siblings, so deleting the
        captions between three tables silently fuses them into one.
      * A `w:sectPr` in a paragraph's `w:pPr` is the section break that *ends*
        the section that paragraph belongs to. Delete the paragraph and the
        break goes with it, and everything before it joins the following
        section — one landscape table page at the end of a manuscript is enough
        to turn the whole document landscape.

    So the block is bounded here, and only the block is removed. It ends at the
    first thing that plainly is not a reference: a table, a Tables/Figures/
    Appendix heading, a figure or table caption, or a paragraph carrying an
    image. Trailing blank paragraphs are left where they are — they are the
    author's page-break spacing, not part of the bibliography.
    """
    paragraphs = list(doc.paragraphs)
    end = biblio_idx + 1  # the heading itself always goes
    for i in range(biblio_idx + 1, len(paragraphs)):
        el = paragraphs[i]._element
        prev = el.getprevious()
        if prev is not None and prev.tag == f"{W}tbl":
            break
        text = WEIRD_SPACE.sub(" ", paragraphs[i].text)
        if STOP_HEADINGS.match(text):
            break
        if not LEAD.match(text) and CAPTION.match(text):
            break
        if _has_image(el):
            break
        if text.strip():
            end = i + 1
    return end


def remove_range(doc, start: int, end: int) -> None:
    """Remove paragraphs [start, end), keeping any section break they carry.

    A paragraph holding a `w:sectPr` is emptied rather than removed, because the
    break describes the page setup of everything *before* it (see
    biblio_end_index). What is left is one empty paragraph carrying the break —
    the same section boundary, in the same place, with nothing of the
    bibliography still in it.
    """
    for p in list(doc.paragraphs)[start:end]:
        el = p._element
        pPr = el.find(f"{W}pPr")
        sect = pPr.find(f"{W}sectPr") if pPr is not None else None
        if sect is None:
            el.getparent().remove(el)
            continue
        for child in list(el):
            if child is not pPr:
                el.remove(child)
        for child in list(pPr):
            if child is not sect:
                pPr.remove(child)

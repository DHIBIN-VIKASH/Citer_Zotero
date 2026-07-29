"""Differential test: web/src/extractor.js vs zotprep/extractor.py.

Every field of every ParsedRef is compared, not just the ones a given reference
happens to populate — a port that quietly leaves `issue` as "" instead of None,
or returns `0` where the original returns None, fails here.

The corpus is deliberately wider than the 35 real references, because those
exercise only the paths a well-formed bibliography takes. The generated bank
below walks each branch of parse_reference() on purpose:

  * Vancouver journal articles, with and without issue, with page ranges that do
    and do not need expanding (2437-60 -> 2437-2460, e1339-51 -> e1339-e1351)
  * consortium/corporate authors, which take the CORPORATE_HINT path
  * books with both ";" and "," before the year, standalone and inline editions
  * book chapters ("In: ..., eds.") with and without a page range
  * references carrying a DOI, a PMID, a PMCID, or several at once
  * the no-locator fallbacks: bare-year tail, and no tail at all
  * accented, non-Latin and astral author names, which is where Python's
    Unicode-aware \\w and \\b diverge from JavaScript's ASCII ones
  * the dash and quote zoo that survives copy-paste out of a PDF

Run:  python web/tools/parity/extractor_parity.py
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from harness import ROOT, run_node  # noqa: E402

from zotprep.extractor import _nfkc, expand_page, parse_reference  # noqa: E402

FIELDS = [
    "doi", "pmid", "pmcid", "authors", "corporate", "title", "journal", "year",
    "volume", "issue", "first_page", "last_page", "is_book", "is_chapter",
    "book_title", "publisher", "place", "edition",
]

# --- generated reference bank -------------------------------------------------

JOURNAL_REFS = [
    "1. Barro RJ, Sala-i-Martin X. Convergence. J Polit Econ. 1992;100(2):223-51.",
    "2. Marmot M. Social determinants of health inequalities. Lancet. 2005;365(9464):1099-104.",
    "3. Smith AB. A short title. N Engl J Med. 2001;344:1.",
    "4. Smith AB, Jones CD. Title with: a colon inside. BMJ. 2010;340:c2289.",
    "5. Doe J. Electronic pages. PLoS Med. 2012;9(11):e1001339-51.",
    "6. Doe J. Supplement volume. Vaccine. 2004;S2:11-44.",
    "7. Lee K, Park S, Choi H, et al. Many authors here. Lancet. 2017;390(10111):2437-60.",
    "8. Sala-i-Martin X. The classical approach to convergence analysis. Econ J. 1996;106(437):1019-36.",
    "9. van den Heuvel-Eibrink MM, de Vries AC. Particle surnames. Blood. 2015;125:1.",
    "10. O'Brien P, D'Angelo R. Apostrophes in names. Gut. 1999;44(3):301-9.",
    "11. Ibáñez J, Müller-Lyer F, Ångström A. Accented surnames. Eur J Phys. 2003;24:117-22.",
    "12. Smith AB. No issue number. Nature. 2020;580:1-5.",
    "13. Smith AB. Title. Lancet 2017;390(10111):2437-60.",
    "14. Smith AB. Journal in tail segment only. 2017;390:2437-60.",
    # bare-year tail, the no-locator fallback
    "15. GBD 2021 Diseases and Injuries Collaborators. Global incidence, prevalence, "
    "years lived with disability. Lancet. 2024.",
    "16. Smith AB. No tail at all and no year anywhere",
    "17. Smith AB. Title only with a year 1998 inside the text.",
    # identifiers
    "18. Smith AB. With a doi. Lancet. 2019;393:1. doi:10.1016/S0140-6736(19)30041-8.",
    "19. Smith AB. With a PMID. Lancet. 2019;393:1. PMID: 30712900.",
    "20. Smith AB. With a PMCID. Lancet. 2019;393:1. PMC6293056.",
    "21. Smith AB. All three. Lancet. 2019;393:1. doi: 10.1016/j.x.2019.01.001 PMID:30712900 PMC6293056.",
    "22. Smith AB. Available from: https://example.org/paper. Accessed 2020. Lancet. 2019;393:1.",
    "23. Smith AB. Editorial note. [Zotero: complete vol/pages/DOI] Lancet. 2024.",
]

BOOK_REFS = [
    "24. Theil H. Economics and Information Theory. Amsterdam: North-Holland; 1967.",
    "25. Cowell FA. Measuring Inequality. 3rd ed. Oxford: Oxford University Press; 2011.",
    "26. Cowell FA. Measuring Inequality. 3rd edn. Oxford: Oxford University Press; 2011.",
    "27. Sen A. On Economic Inequality. Oxford: Clarendon Press, 1973.",
    "28. Atkinson AB. Inequality, revised edn. Cambridge: Harvard University Press; 2015.",
    "29. Author A. Chapter title. In: Editor B, Editor C, eds. The Big Book. "
    "Amsterdam: Elsevier, 2013: 1113-36.",
    "30. Author A. Chapter title. In: Editor B, ed. The Big Book, 4th edn. "
    "Oxford: Oxford University Press; 2009: 55-61.",
    "31. Author A. Chapter with no pages. In: Editor B, eds. Another Book. "
    "London: Routledge; 2001.",
    "32. Institute of Medicine. A report title. Washington, DC: National Academies Press; 2011.",
]

CORPORATE_REFS = [
    "33. India State-Level Disease Burden Initiative Collaborators. Nations within a nation. "
    "Lancet. 2017;390(10111):2437-60.",
    "34. World Health Organization. Global tuberculosis report. Geneva: WHO; 2023.",
    "35. GBD 2021 Low Back Pain Collaborators. Global, regional, and national burden. "
    "Lancet Rheumatol. 2023;5(6):e316-29.",
    "36. UNICEF. State of the world's children. New York: UNICEF; 2021.",
    "37. The ARDS Network Investigators. Ventilation with lower tidal volumes. "
    "N Engl J Med. 2000;342(18):1301-8.",
    "38. WHO Study Team. A study. Lancet. 2005;365:1.",
]

# References written to exercise one specific divergence each. These exist
# because mutation testing (tools/parity/mutation_check.py) showed the corpus
# above giving a clean pass to ports that were provably wrong: real
# bibliographies are too well-formed to reach these branches by accident.
TARGETED_REFS = [
    # Python's str.replace strips EVERY occurrence of the DOI, JS's strips one
    "62. Smith AB. Title. Lancet. 2019;393:1. 10.1016/j.x.2019.01.001 10.1016/j.x.2019.01.001.",
    "63. Smith AB. 10.1016/j.y.2019.01.001 repeated 10.1016/j.y.2019.01.001 again. Lancet. 2019;393:1.",
    # Unicode word boundaries: an accented letter is a word character to Python,
    # so \bPMID\b does NOT match here — but an ASCII \b would
    "64. Smith AB. Title. Lancet. 2019;393:1. éPMID: 30712900.",
    "65. Smith AB. Title. Lancet. 2019;393:1. PMID: 30712900é.",
    "66. Smith AB. Title. Lancet. 2019;393:1. étPMC6293056.",
    "67. Smith AB. Title. Lancet. 2019;393:1. PMC6293056é.",
    "68. Smith AB. Titleé10.1016/j.z.2019.01.001. Lancet. 2019;393:1.",
    # \w inside the author token: a multi-word surname whose first word carries
    # a non-ASCII letter. With ASCII \w the whole token fails to match and the
    # fallback keeps only the first word, losing "Volhard".
    "69. Nüßlein Volhard C, Wieschaus EF. Mutations affecting segment number. Nature. 1980;287:795-801.",
    "70. Ångström Bohr N, Planck MK. Two word accented surnames. Ann Phys. 1901;4:553-63.",
    "71. Öztürk Yılmaz A, Şahin B. Turkish surnames. Turk J Med. 2015;45:1-9.",
    # title left ending in a comma/semicolon, where trim() != strip(" .,;")
    "72. Smith AB. A title that ends in a comma, Lancet. 2020;1:1.",
    "73. Smith AB. A title that ends in a semicolon; Lancet. 2020;1:1.",
    "74. Smith AB. ,,Leading and trailing punctuation,, Lancet. 2020;1:1.",
    # author-list vote with an odd chunk count and a corporate hint: floor(3/2)=1
    # accepts, 3/2=1.5 rejects
    "75. Smith AB, The Big Study Group, Another Thing. A title. Lancet. 2020;1:1.",
    "76. Smith AB, Jones CD, The Network. A title. Lancet. 2020;1:1.",
    "77. Smith AB, A Consortium, B Consortium, C Consortium, D Consortium. T. Lancet. 2020;1:1.",
    # volume shapes that separate a lazy from a greedy quantifier
    "78. Smith AB. T. Lancet. 2020;123456789:1.",
    "79. Smith AB. T. Lancet. 2020;12345678:1-9.",
    "80. Smith AB. T. Lancet. 2020;A1B2C3D4:1.",
    "81. Smith AB. T. Lancet. 2020;  390  ( 10111 ) : 2437-60.",
    # multiple trailing dots, on the no-tail path where rstrip(".") runs
    "82. Smith AB. A title with no tail at all...",
    "83. Smith AB. Another untailed title..",
    # several years and no locator: the fallback must take the LAST one
    "84. Smith AB. A 1998 study revisited in 2005 and again in 2011",
    "85. Smith AB. Comparing 1990 with 2016 outcomes",
    # two bare-year tail segments: the fallback scans backwards
    "86. Smith AB. Title. 2001. Lancet. 2020.",
    "87. Smith AB. Title. 1999. Some Journal. 2003.",
    # corporate hint present but the segment also parses as an author list
    "88. WHO Collaborating Group. A title. Lancet. 2020;1:1.",
    "89. Who AB, Group CD. Looks like authors despite hints. Lancet. 2020;1:1.",
    # Titles left with punctuation at an end after the sentence split, which is
    # where `strip(" .,;")` and `trim()` part company. A segment only ever ends
    # in ".", "?" or "!" (that is where the split happens), so reaching a
    # trailing comma takes a ",." — rare in the wild, routine in bad paste-ups.
    "90. Smith AB. Title one,. Lancet. 2020;1:1.",
    "91. Smith AB. Title two;. Lancet. 2020;1:1.",
    "92. Smith AB. ,Title with a leading comma. Lancet. 2020;1:1.",
    "93. Smith AB. ;Title with a leading semicolon. Lancet. 2020;1:1.",
    "94. Smith AB. ,. Lancet. 2020;1:1.",
    "95. Smith AB. , Title spaced comma ,. Lancet. 2020;1:1.",
    "96. Smith AB. Title,. Journal,. 2020;1:1.",
    # ... and the same for the book-chapter exit, which returns early and so
    # runs its own copy of the normalise-and-strip step
    "97. Author A. Chapter title,. In: Editor B, eds. The Big Book. "
    "Amsterdam: Elsevier, 2013: 1113-36.",
    "98. Author A. ,Leading comma chapter. In: Editor B, eds. Another Book. "
    "London: Routledge; 2001.",
    "99. Author A. Chapter title;. In: Editor B, Editor C, eds. Third Book, 2nd edn. "
    "Oxford: Oxford University Press; 2009: 55-61.",
    "100. Author A. Chapter,. In: Ed B, ed. Book,. Boston: Pub, 1999: 1-9.",
]

EDGE_REFS = [
    "", " ", ".", "1.", "1. ", "39.", "No leading number here. Lancet. 2020;1:1.",
    "[40] Bracketed number. Lancet. 2020;1:1.",
    "(41) Parenthesised number. Lancet. 2020;1:1.",
    "42. Smith AB. Title with an en–dash and “quotes”. Lancet. 2020;1:1–10.",
    "43. Smith AB. Title with a minus − sign. Lancet. 2020;1:1.",
    "44. 北京大学. A CJK corporate author. Lancet. 2020;1:1.",
    "45. Иванов АБ. Cyrillic authors. Lancet. 2020;1:1.",
    "46. Smith AB. Astral \U0001d49c character in title. Lancet. 2020;1:1.",
    "47. Smith AB. Emoji \U0001f600 in title. Lancet. 2020;1:1.",
    "48. Smith AB, et al. Trailing et al. Lancet. 2020;1:1.",
    "49. Smith AB et al. Trailing et al without comma. Lancet. 2020;1:1.",
    "50. Smith, Alan B. APA style author. Lancet. 2020;1:1.",
    "51. A. Smith. Initials first. Lancet. 2020;1:1.",
    "52. Smith AB.Title with no space after period. Lancet. 2020;1:1.",
    "53.    Smith AB.   Extra    whitespace   everywhere.   Lancet.  2020;1:1.  ",
    "54. Smith AB. Question mark in title? Lancet. 2020;1:1.",
    "55. Smith AB. Exclamation! Lancet. 2020;1:1.",
    # a title that itself looks like an author list
    "56. Smith AB. Jones CD, Brown EF. Lancet. 2020;1:1.",
    # page expansions of every shape
    "57. Smith AB. T. Lancet. 2020;1:9-12.",
    "58. Smith AB. T. Lancet. 2020;1:99-101.",
    "59. Smith AB. T. Lancet. 2020;1:1234-5.",
    "60. Smith AB. T. Lancet. 2020;1:e1339-51.",
    "61. Smith AB. T. Lancet. 2020;1:e9-e12.",
]

NFKC_CASES = [
    "", " ", "a‐b", "a‑b", "a‒b", "a–b", "a—b",
    "a―b", "a−b", "‘q’", "“q”",
    "Ｆｕｌｌｗｉｄｔｈ",
    "ﬁbrosis", "H₂O", "x²", "½", "№ 5",
    "  collapse   \t all \n whitespace  ", " nbsp ",
    "\U0001d49c astral", "café", "café",
]

PAGE_CASES = [
    (None, None), ("1", None), (None, "1"), ("", ""), ("1", "2"),
    ("9", "12"), ("99", "101"), ("1234", "5"), ("2437", "60"),
    ("e1339", "51"), ("e9", "e12"), ("e1339", "e1351"), ("S1", "5"),
    ("abc", "def"), ("1a", "2b"), ("1", "abc"), ("0017", "23"),
]


def mutate(ref: str, rng: random.Random) -> str:
    """Small perturbations of the shapes a bibliography actually varies in."""
    ops = [
        lambda s: s.replace(". ", ".  "),
        lambda s: s.replace(";", " ; "),
        lambda s: s.replace(":", " : "),
        lambda s: s.replace("-", "–"),
        lambda s: s.replace(",", ", "),
        lambda s: s.upper(),
        lambda s: s.lower(),
        lambda s: s.rstrip("."),
        lambda s: s + " ",
        lambda s: " " + s,
        lambda s: s.replace(" et al", " et al."),
        lambda s: s.replace("Lancet", "The Lancet"),
    ]
    return rng.choice(ops)(ref)


def build_refs(seed: int) -> list[str]:
    rng = random.Random(seed)
    refs = list(JOURNAL_REFS + BOOK_REFS + CORPORATE_REFS + TARGETED_REFS + EDGE_REFS)

    corpus = ROOT / "tests" / "refs_35.json"
    if corpus.exists():
        refs.extend(json.loads(corpus.read_text(encoding="utf-8")))

    base = list(refs)
    for _ in range(1500):
        r = rng.choice(base)
        for _ in range(rng.randrange(1, 3)):
            r = mutate(r, rng)
        refs.append(r)
    return refs


def dump_py(ref) -> dict:
    out = {f: getattr(ref, f) for f in FIELDS}
    out["has_identifier"] = ref.has_identifier
    out["lead_author"] = ref.lead_author
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260729)
    ap.add_argument("--show", type=int, default=8)
    args = ap.parse_args()

    refs = build_refs(args.seed)
    want = [dump_py(parse_reference(i + 1, r)) for i, r in enumerate(refs)]
    want_nfkc = [_nfkc(s) for s in NFKC_CASES]
    want_pages = [expand_page(a, b) for a, b in PAGE_CASES]

    got = run_node("extractor_runner.mjs", {
        "refs": refs,
        "nfkcCases": NFKC_CASES,
        "pageCases": [list(p) for p in PAGE_CASES],
    })

    failures: list[tuple[str, str, object, object]] = []

    for raw, w, g in zip(refs, want, got["parsed"]):
        for f in list(FIELDS) + ["has_identifier", "lead_author"]:
            if w[f] != g.get(f):
                failures.append((raw, f, w[f], g.get(f)))

    for s, w, g in zip(NFKC_CASES, want_nfkc, got["nfkc"]):
        if w != g:
            failures.append((s, "_nfkc", w, g))

    for (a, b), w, g in zip(PAGE_CASES, want_pages, got["pages"]):
        if w != g:
            failures.append((f"{a!r},{b!r}", "expand_page", w, g))

    total = len(refs) * (len(FIELDS) + 2) + len(NFKC_CASES) + len(PAGE_CASES)
    if not failures:
        print(f"PASS  extractor: {len(refs)} references, {total} field comparisons, 0 mismatches")
        return 0

    print(f"FAIL  extractor: {len(failures)}/{total} field mismatches")
    by_field: dict[str, int] = {}
    for _, f, _, _ in failures:
        by_field[f] = by_field.get(f, 0) + 1
    for f, n in sorted(by_field.items(), key=lambda kv: -kv[1]):
        print(f"    {f:<16} {n}")
    for raw, f, w, g in failures[: args.show]:
        print(f"\n  field {f}")
        print(f"    ref    = {ascii(raw)[:160]}")
        print(f"    python = {ascii(w) if isinstance(w, str) else w!r}")
        print(f"    js     = {ascii(g) if isinstance(g, str) else g!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

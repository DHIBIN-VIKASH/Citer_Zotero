"""Differential test: web/src/utils.js vs zotprep/utils.py.

These are the primitives the accept gate is made of, so every one of them is
compared on the real corpus plus a bank of cases chosen to hit the specific
places where Python and JavaScript string semantics diverge:

  Unicode-aware vs ASCII classes  Python `\\w`, `\\b` and `str.islower()` cover
                                  accented letters; JS `\\w`/`\\b` do not. Probed
                                  with accented and non-Latin surnames.
  NFD / combining marks           `strip_accents` drops `unicodedata.combining`
                                  characters; the port uses `\\p{M}`. Probed with
                                  precomposed and decomposed spellings of the
                                  same name, plus Hangul and CJK.
  NFKC folding                    ligatures, full-width forms, superscripts.
  multi-char strip                `str.strip(" .,;")` vs `String.trim()`.
  astral plane                    Python indexes code points, JS code units.

Run:  python web/tools/parity/utils_parity.py
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from harness import ROOT, report, run_node, tag  # noqa: E402

from zotprep import utils  # noqa: E402
from zotprep.extractor import parse_reference  # noqa: E402

PY = {
    "strip_accents": utils.strip_accents,
    "norm_text": utils.norm_text,
    "title_similarity": utils.title_similarity,
    "norm_doi": utils.norm_doi,
    "norm_surname": utils.norm_surname,
    "surname_of": utils.surname_of,
    "journal_match": utils.journal_match,
    "page_equal": utils.page_equal,
    "volume_equal": utils.volume_equal,
    "strip_chars": lambda s, c: s.strip(c),
    "rstrip_chars": lambda s, c: s.rstrip(c),
}

# --- case banks ---------------------------------------------------------------

TEXTS = [
    "", " ", "  ", "\t\n", ".", " .,; ", "a", "The Lancet",
    "Nations within a nation: variations in epidemiological transition",
    "Renal osteodystrophy and chronic kidney disease-mineral bone disorder",
    "<i>Mycobacterium</i> tuberculosis and the <sub>2</sub> effect",
    "AT&amp;T &lt;i&gt; &quot;quoted&quot; &apos;",
    "Effet de l'âge — a naïve Zürich café study",
    # same text, precomposed vs decomposed: NFD/combining parity
    "Ångström Zoë Ibáñez", "Ångström Zöe Ibáñez",
    # NFKC folding targets
    "ﬁbrosis ﬂow", "Ｆｕｌｌｗｉｄｔｈ Ｔｉｔｌｅ", "H₂O and x², №5, ½ dose",
    " non breaking space ", "zero​width",
    # non-Latin
    "北京大学学报", "Радиология и онкология", "الطب الباطني", "한국의학회지",
    # astral
    "𝒜 study of 𝕏", "emoji 😀 in a title",
    # dashes and quotes
    "2437–60 en-dash", "it’s a “quoted” word",
    "10.1016/S0140-6736(17)32804-0", "https://doi.org/10.1016/j.x.2020.01.001",
    "DOI: 10.1234/abc.", "doi:10.1234/ABC-def...",
]

SURNAMES = [
    "", " ", "Marmot M", "Gupta, Prakash C", "Prakash C Gupta",
    "Xavier Sala-i-Martin", "Sala-i-Martin X", "Ludwig van Beethoven",
    "van der Berg A", "Jacques Vallin", "de la Cruz M", "O'Brien P",
    "D'Angelo-Smith R", "Ibáñez J", "Ångström A", "Müller-Lyer F",
    "GBD 2021 Collaborators", "WHO", "Li", "Wu X Y Z", "MacDonald-Smith AB",
    "île de France", "van den Heuvel-Eibrink MM",
]

JOURNALS = [
    ("J Polit Econ", ["Journal of Political Economy", None]),
    ("J R Stat Soc Series B", ["Journal of the Royal Statistical Society: Series B (Methodological)", None]),
    ("Bull World Health Organ", ["Bulletin of the World Health Organization", None]),
    ("Lancet", ["The Lancet", "Lancet"]),
    ("Lancet", ["Lancet Oncology", None]),
    ("Lancet", ["Lancet Glob Health", None]),
    ("Lancet Glob Health", ["The Lancet Global Health", None]),
    ("N Engl J Med", ["The New England Journal of Medicine", None]),
    ("BMJ", ["BMJ", "British Medical Journal"]),
    ("", ["The Lancet", None]),
    ("Econ J", ["The Economic Journal", None]),
    ("Int J Epidemiol", ["International Journal of Epidemiology", None]),
    ("PLoS Med", ["PLoS Medicine", "PLoS Med"]),
    ("Zeitschr fur Physik", ["Zeitschrift für Physik", None]),
    ("Rev Med Interne", ["La Revue de Médecine Interne", None]),
    # Cases where dropping the post-colon qualifier is the *only* route to a
    # match — the parenthesis-stripped variant still has the wrong token count,
    # so these are what actually exercise the `split(":")[0]` branch.
    ("J R Stat Soc", ["Journal of the Royal Statistical Society: Series B", None]),
    ("Cell Metab", ["Cell Metabolism: Reviews", None]),
    ("Nat Rev Cancer", ["Nature Reviews Cancer: Perspectives", None]),
    ("BMJ", ["BMJ: British Medical Journal", None]),
    # ... and the reverse, where splitting must NOT rescue a wrong journal
    ("Lancet Oncol", ["Lancet: Oncology and Haematology", None]),
    # The prefix test is symmetric — `cand.startswith(ref) or ref.startswith(cand)`
    # — because either side can be the abbreviated one. These are the reversed
    # pairs, where the *reference* holds the full name and the candidate the
    # abbreviation; without the second half of that test they all fail.
    ("Journal of Political Economy", ["J Polit Econ", None]),
    ("Bulletin of the World Health Organization", ["Bull World Health Organ", None]),
    ("The New England Journal of Medicine", ["N Engl J Med", None]),
    ("International Journal of Epidemiology", ["Int J Epidemiol", None]),
    ("Nature Reviews Cancer", ["Nat Rev Cancer", None]),
    ("The Lancet Global Health", ["Lancet Glob Health", None]),
]

LOCATORS = [
    None, "", " ", "10111", "390", "S2", "e1339", "e1339-51", "2437",
    "2437-60", "17", "17-23", "III", "iii", "10-A", "10 A", "0017",
    "Suppl 2", "e1339 ", " e1339", "E1339",
]

# `str.strip(chars)` cases. These exist as their own bank because the corpus
# does not supply them: real titles rarely begin with a strippable character, so
# an earlier version of this harness exercised only the trailing end and gave a
# clean pass to a port that never stripped the leading one. Each case here has
# strippable characters at the *start*, at the *end*, and at both, so a
# one-sided implementation cannot survive.
STRIP_CASES = [
    " leading space", "leading space ", " both sides ",
    ".leading dot", "trailing dot.", ".both.", "...ellipsis...",
    ",comma", "comma,", ";semi", "semi;",
    " .,;mixed leading", "mixed trailing .,; ", " .,; mixed both .,; ",
    "  Some Title, ", " . , ; ", ".,;", " ", "", "a", " a ", ". a .",
    "  Nations within a nation: variations  ", "J Polit Econ.",
    "-dash-", " -dash- ",
]

DOIS = [
    None, "", "10.1016/S0140-6736(17)32804-0",
    "https://doi.org/10.1016/S0140-6736(17)32804-0",
    "http://dx.doi.org/10.1234/ABC", "DOI: 10.1234/abc", "doi:  10.1234/abc",
    "10.1234/abc.", "10.1234/abc...", "  10.1234/ABC  ", "not-a-doi",
]


def corpus_strings() -> list[str]:
    """Every field the real extractor pulls out of the reference corpus."""
    out: list[str] = []
    path = ROOT / "tests" / "refs_35.json"
    if not path.exists():
        return out
    for i, raw in enumerate(json.loads(path.read_text(encoding="utf-8")), 1):
        out.append(raw)
        ref = parse_reference(i, raw)
        out.extend(
            x for x in [ref.title, ref.journal, ref.corporate, ref.book_title,
                        ref.publisher, ref.place, ref.volume, ref.issue,
                        ref.first_page, ref.last_page] if x
        )
        out.extend(ref.authors or [])
    return out


def build_calls(seed: int) -> list[dict]:
    rng = random.Random(seed)
    corpus = corpus_strings()
    texts = TEXTS + corpus
    calls: list[dict] = []

    for s in texts:
        calls.append({"fn": "strip_accents", "args": [s]})
        calls.append({"fn": "norm_text", "args": [s]})
        calls.append({"fn": "norm_surname", "args": [s]})
        calls.append({"fn": "surname_of", "args": [s]})
    for s in STRIP_CASES + texts:
        for chars in [" .,;", ".", " ", ",;", "", "-", " ."]:
            calls.append({"fn": "strip_chars", "args": [s, chars]})
            calls.append({"fn": "rstrip_chars", "args": [s, chars]})

    for s in SURNAMES:
        calls.append({"fn": "surname_of", "args": [s]})
        calls.append({"fn": "norm_surname", "args": [s]})

    for d in DOIS:
        calls.append({"fn": "norm_doi", "args": [d]})

    for a in LOCATORS:
        for b in LOCATORS:
            calls.append({"fn": "page_equal", "args": [a, b]})
            calls.append({"fn": "volume_equal", "args": [a, b]})

    for ref, cands in JOURNALS:
        calls.append({"fn": "journal_match", "args": [ref, *cands]})
    # cross product too: most pairs should be False, which is the risky direction
    all_names = [c for _, cands in JOURNALS for c in cands if c]
    for ref, _ in JOURNALS:
        for name in all_names:
            calls.append({"fn": "journal_match", "args": [ref, name, None]})

    # title_similarity over corpus pairs and near-miss mutations
    titles = [t for t in texts if t and len(t.split()) >= 2]
    for t in titles:
        calls.append({"fn": "title_similarity", "args": [t, t]})
    for _ in range(4000):
        a = rng.choice(titles)
        b = rng.choice(titles)
        calls.append({"fn": "title_similarity", "args": [a, b]})
    for _ in range(2000):
        a = rng.choice(titles)
        toks = a.split()
        if len(toks) > 2:
            toks.pop(rng.randrange(len(toks)))
        calls.append({"fn": "title_similarity", "args": [a, " ".join(toks)]})

    return calls


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260729)
    args = ap.parse_args()

    calls = build_calls(args.seed)
    want = []
    for c in calls:
        try:
            want.append(tag(PY[c["fn"]](*c["args"])))
        except Exception as e:  # noqa: BLE001 - mirrored by the runner's catch
            want.append({"t": "e", "v": str(e)})
    got = run_node("utils_runner.mjs", {"calls": calls})
    return report("utils", calls, want, got)


if __name__ == "__main__":
    sys.exit(main())

"""Check the journal locator against the shapes medical Vancouver actually uses.

The locator is `journal. year;volume(issue):pages`, and it carries three of the
four fields the accept gate compares. When the pattern misses, it does not
degrade the match — it removes it: journal, volume and first page all come back
empty, the gate has nothing left to agree on, and a perfectly ordinary reference
is rejected. Three references in one manuscript failed exactly this way.

    python tests/locator_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zotprep.extractor import parse_reference  # noqa: E402

# (reference, journal, volume, issue, first page, last page)
CASES = [
    # the plain form
    ("Barro RJ. Convergence. J Polit Econ. 1992;100(2):223-51.",
     "J Polit Econ", "100", "2", "223", "251"),
    ("Smith A. A paper. Ann Transl Med. 2012;10:1.",
     "Ann Transl Med", "10", None, "1", None),
    # supplements: the space inside the volume is the trap, and the supplement
    # number must not be read as part of the volume — indexes store "63"
    ("Hawker GA, Mian S. Measures of adult pain. Arthritis Care Res (Hoboken). "
     "2011;63 Suppl 11:S240-52.",
     "Arthritis Care Res (Hoboken)", "63", None, "S240", "S252"),
    ("Manchikanti L, Singh V. Epidemiology of low back pain in adults. "
     "Neuromodulation. 2014;17 Suppl 2:3-10.",
     "Neuromodulation", "17", None, "3", "10"),
    ("Lee K. A paper. Spine J. 2020;20 Suppl:12-9.",
     "Spine J", "20", None, "12", "19"),
    # two-letter page prefixes, as used by the JCDR family
    ("Bhatia R, Chopra G. Efficacy of platelet rich plasma via lumbar epidural route. "
     "J Clin Diagn Res. 2016;10(9):UC05-7.",
     "J Clin Diagn Res", "10", "9", "UC05", "UC07"),
    # one-letter prefixes and volume letters, which already worked and must stay
    ("Jones C. A paper. Eur Spine J. 2019;28(4):e10-e18.",
     "Eur Spine J", "28", "4", "e10", "e18"),
    ("Park S. A paper. J Test. 2004;S2:11-44.",
     "J Test", "S2", None, "11", "44"),
    # lettered volumes, as the Bone & Joint Journal numbers them. Crossref
    # stores the suffix too ("100-B"), so it stays part of the volume.
    ("Findlay C, Ayis S, Demetriades AK. Total disc replacement versus anterior cervical "
     "discectomy and fusion. Bone Joint J. 2018;100-B(8):991-1001.",
     "Bone Joint J", "100-B", "8", "991", "1001"),
    ("Hou Y, Nie L, Pan X. Effectiveness and safety of Mobi-C. Bone Joint J. 2016;98-B(6):829-833.",
     "Bone Joint J", "98-B", "6", "829", "833"),
]


def main() -> int:
    failures = []
    for raw, journal, vol, issue, fp, lp in CASES:
        ref = parse_reference(1, raw)
        got = (ref.journal, ref.volume, ref.issue, ref.first_page, ref.last_page)
        want = (journal, vol, issue, fp, lp)
        if got != want:
            failures.append(f"  {raw[:60]}...\n    want {want}\n    got  {got}")

    if failures:
        print(f"FAIL  locator: {len(failures)}/{len(CASES)} references misparsed")
        print("\n".join(failures))
        return 1
    print(f"PASS  locator: {len(CASES)} references, every field as written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

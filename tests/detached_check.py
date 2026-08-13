"""Check the space-separated citation notation against the prose it looks like.

A superscript citation that loses its formatting leaves bare digits behind:

    "...in adults aged 45 years and older. 1 According to..."
    "...short-lived analgesia 6,7."

Read literally that is a word, a space and some digits — which is also what
"25 patients", "Group 4" and "for 12 months" are. So each guard in
`_detached_spans` is asserted here in both directions: the citation it must
find, and the sentence it must leave alone. Every FOUND line below comes from a
manuscript the tool failed on; every IGNORED line comes from one it damaged or
would have.

    python tests/detached_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zotprep.docx_writer import _detached_spans  # noqa: E402

FOUND = [
    # (sentence, the specs that must come out of it)
    ("in adults aged 45 years and older. 1 According to the Global Burden", ["1"]),
    ("the disease burden being for knee OA 2.", ["2"]),
    ("result in pain, stiffness, and functional decline 3.", ["3"]),
    ("induce rapid but short-lived analgesia 6,7. Randomised evidence", ["6,7"]),
    # a unit that has already taken its own number; the digits after it are not
    # part of the measurement
    ("no sustained benefit beyond 3 months 6,8. This has led", ["6,8"]),
    # mid-sentence, which only the comma-separated shape earns
    ("more than two of the agents at a single time 20,21 and few studies", ["20,21"]),
    ("tumour necrosis factor alpha 10,11,36), at the local level.", ["10,11,36"]),
    ("standardised iPRF preparation protocols 18,19.", ["18,19"]),
    ("for intermediate-to-long-term knee OA management 21,37,40.", ["21,37,40"]),
    ("alternatives with potentially disease-modifying effects 9.", ["9"]),
]

IGNORED = [
    # the number labels what precedes it
    "Group 4 (corticosteroid) experienced some loss to follow-up.",
    "patients with primary knee OA of Kellgren-Lawrence grade 2 or 3.",
    "the improvement is shown in Table 2 and Figure 3.",
    "By month 1, VAS had fallen to 4.25.",
    "at baseline (month 0) and at months 1, 3, 6 and 12 after injection.",
    # function words a citation never follows
    "40 mL of peripheral venous blood was collected in ACD tubes.",
    "randomly divided into 4 groups of 25 patients each.",
    "For iPRF, 20 mL of blood was collected.",
    # the digits are being measured
    "leukocyte concentrations 4-6 times baseline; thus, it was LR-PRP.",
    "Thirty-seven studies comprising 4,326 patients provided both arms.",
    "the 0-10 Visual Analogue Scale for pain and the 0-96 WOMAC index.",
    "prolonging ATT beyond 6-9 months conferred no measurable benefit.",
    # a decimal, not a sentence that ended
    "largest for Group 4 (baseline 62.14 at 1 month).",
    # a list that runs backwards, or repeats itself, is a list of results
    "Groups 1 to 4 had 2, 3, 1 and 4 losses to follow-up respectively.",
    "outcomes worsened 12,9.",
    "reported in three cohorts 8,8 and one registry.",
    # no reference is numbered zero
    "scores were recorded at week 0, 4 and 8.",
    "response was graded 0,1.",
    # the list continues past the digits into what is being measured
    "outcomes improved 1, 3, 6 and 12 months later.",
    "the pooled cohort included 4,326.",
    # a bare number mid-sentence, which no guard but the shape rule rejects
    "the response improved 12 percentage units overall.",
    "There were no citations in this sentence at all.",
]


def main() -> int:
    failures = []

    for text, want in FOUND:
        got = [spec for _, _, spec in _detached_spans(text)]
        if got != want:
            failures.append(f"  missed   {text!r}\n    want {want!r}, got {got!r}")

    for text in IGNORED:
        got = [spec for _, _, spec in _detached_spans(text)]
        if got:
            failures.append(f"  invented {text!r}\n    got {got!r}")

    # Offsets are part of the contract: the spec is used to look references up,
    # but the span is what gets overwritten in the manuscript.
    text = "short-lived analgesia 6,7. Randomised evidence"
    spans = _detached_spans(text)
    if spans and text[spans[0][0]:spans[0][1]] != "6,7":
        failures.append(f"  span misaligned: {text[spans[0][0]:spans[0][1]]!r}")

    total = len(FOUND) + len(IGNORED) + 1
    if failures:
        print(f"FAIL  detached: {len(failures)}/{total} cases")
        print("\n".join(failures))
        return 1
    print(f"PASS  detached: {total} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

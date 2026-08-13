"""Differential test: detachedSpans() in docx.js vs _detached_spans() in docx_writer.py.

The space-separated notation is the one place the citation scanner has to guess:
"analgesia 6,7." is a citation and "25 patients were assigned" is not, and both
are a word, a space and some digits. The rule that separates them is a stack of
guards, and a guard that disagrees between the two engines means the browser
would rewrite a sentence the CLI leaves alone.

Two corpora feed it:

  * every body paragraph of the manuscripts in the repository, so the real
    prose the guards were tuned against is covered;
  * a bank of sentences aimed at each guard individually — timepoint labels,
    thousands separators, decimals, descending lists, trailing units — because
    a corpus that happens to lack a shape proves nothing about it.

Run:  python web/tools/parity/detached_parity.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from harness import ROOT, report, run_node, tag  # noqa: E402

from zotprep.docx_writer import _detached_spans  # noqa: E402

# --- the guard bank -----------------------------------------------------------
# Each line names the guard it exercises. Expected behaviour is not asserted
# here — that is the unit test's job. This file asserts only that both engines
# agree, which is why a line can sit here without a comment saying which way it
# should go.
CASES = [
    # the notation itself
    "in adults aged 45 years and older. 1 According to the Global Burden of Disease",
    "with most of the disease burden being for knee OA 2.",
    "induce rapid but short-lived analgesia 6,7. Randomised evidence has suggested",
    "no sustained benefit beyond 3 months 6,8. This has led to an interest",
    "compare more than two of the agents at a single time 20,21 and few studies",
    "tumour necrosis factor alpha 10,11,36), at the local level.",
    "growth factors 12,13, mediated by leukocytes.",
    "for intermediate-to-long-term knee OA management 21,37,40.",
    # label words: the number belongs to the label
    "Group 4 (corticosteroid) experienced some loss to follow-up.",
    "patients with primary knee OA of Kellgren-Lawrence grade 2 or 3.",
    "shown in Table 2 and Figure 3.",
    "randomly divided into 4 groups of 25 patients each.",
    # function words
    "40 mL of peripheral venous blood was collected in ACD tubes.",
    "mean baseline WOMAC scores ranged from 60.77 to 62.14.",
    "improvement of 58% was seen at 12 months.",
    # timepoints, both directions
    "By month 1, VAS had fallen to 4.25.",
    "both instruments at baseline (month 0) and at months 1, 3, 6 and 12 after injection.",
    "outcomes were assessed at 12 months 14,15.",
    # measurements the digits belong to
    "leukocyte concentrations 4-6 times baseline; thus, it was classified LR-PRP.",
    "Thirty-seven studies comprising 4,326 patients provided both arms.",
    "the 0-10 Visual Analogue Scale (VAS) for pain and the 0-96 WOMAC index.",
    "prolonging ATT beyond 6-9 months conferred no measurable benefit.",
    # decimals and exponents
    "largest for Group 4 (baseline 62.14 +/- 5.24 at 1 month).",
    "the difference was significant 12. p=0.032 for the comparison.",
    # list shape
    "Groups 1 to 4 had 2, 3, 1 and 4 losses to follow-up respectively.",
    "reported in three cohorts 8,8 and one registry.",
    "outcomes worsened 12,9.",
    # each of these reaches exactly one guard, so a harness that drops that guard
    # has nothing else to hide behind
    "outcomes improved 1, 3, 6 and 12 months later.",
    "the pooled cohort included 4,326.",
    "response was graded 0,1.",
    "the response improved 12 percentage units overall.",
    "durability profile reported herein 20,21. Finally, some of the comparisons",
    # nothing to find
    "There were no citations in this sentence at all.",
    "",
    "   ",
    # interaction with the other notations, which citationSpans() resolves by
    # overlap but which must be scanned identically on their own
    "as reported previously [12] and elsewhere (13,14).",
    "systems.29-32 differing rates.41-42",
]


def corpus_paragraphs() -> list[str]:
    """Body text of every manuscript checked into the repository."""
    try:
        from docx import Document
    except ImportError:  # pragma: no cover - python-docx is a hard dependency
        return []
    out: list[str] = []
    for path in sorted(ROOT.glob("*.docx")):
        if path.name.startswith("~$"):
            continue
        for p in Document(str(path)).paragraphs:
            if p.text.strip():
                out.append(p.text)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args()

    texts = CASES + corpus_paragraphs()
    calls = [{"fn": "detached_spans", "args": [t]} for t in texts]
    want = [tag([list(s) for s in _detached_spans(t)]) for t in texts]
    got = run_node("detached_runner.mjs", {"calls": calls})
    return report("detached", calls, want, got, show=args.show)


if __name__ == "__main__":
    raise SystemExit(main())

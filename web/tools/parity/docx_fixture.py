"""Build a .docx fixture and record what the Python docx_writer does to it.

The browser port of docx_writer.py cannot be compared by feeding it strings: its
whole difficulty is that Word splits a single citation across runs, stores
superscripts as run formatting rather than characters, and hides the text the
scanner measures behind python-docx's run/paragraph semantics. So the fixture is
a real .docx containing every shape that logic has to survive, and the
comparison is the resulting paragraph text — the thing that actually ships.

Writes:
  web/fixture.docx        the input, for the browser to fetch
  web/fixture.expect.json what Python produces from it

Both are gitignored (*.docx, and the json is written next to it). Run:
    python web/tools/parity/docx_fixture.py
then open the app's page and run the docx parity check from the browser console.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from docx import Document  # noqa: E402

from zotprep.docx_writer import (  # noqa: E402
    find_biblio_index, make_renderer, mark_body, parse_bibliography, remove_from,
)

WEB = ROOT / "web"
SUP = "⁰¹²³⁴⁵⁶⁷⁸⁹"


def sup(n: str) -> str:
    """ASCII digits -> Unicode superscript digits."""
    return "".join(SUP[int(c)] if c.isdigit() else c for c in n)


def build() -> Path:
    doc = Document()
    doc.add_paragraph("A Manuscript With Citations")

    # 1. Unicode superscript citations, single and ranged, incl. the Lancet
    #    middle-dot separator.
    doc.add_paragraph(f"Health systems vary widely.{sup('1')} Others disagree.{sup('2')}⁻{sup('4')}")
    doc.add_paragraph(f"Multiple, dot separated.{sup('1')}·{sup('3')}")

    # 2. Bracketed groups.
    doc.add_paragraph("Bracketed forms are common [1] and also (2,3) and [4-6].")

    # 3. Digits fused onto sentence punctuation — a degraded superscript.
    doc.add_paragraph("Better-resourced systems fare differently.5-7 And again.8")

    # 4. Mathematics that must NOT become citations.
    doc.add_paragraph(f"The rate was -13.3x10⁻{sup('3')} per year, with R{sup('2')}=0.32 and 5 km{sup('2')}.")

    # 5. A citation split across runs, which is why the scan works in paragraph
    #    coordinates rather than per-run.
    p = doc.add_paragraph("Split across runs;")
    r1 = p.add_run(sup("1"))
    r2 = p.add_run(sup("2"))
    r1.font.superscript = False  # already superscript characters
    r2.font.superscript = False
    p.add_run(" continues.")

    # 6. Word superscript *formatting* over ordinary digits.
    p = doc.add_paragraph("Formatted superscript follows this")
    rr = p.add_run("9")
    rr.font.superscript = True
    p.add_run(" and then prose.")

    # 7. An oversized range: a dropped digit, which must be refused.
    doc.add_paragraph(f"A dropped digit produces{sup('3')}⁻{sup('3')}{sup('2')} a huge span.")

    # 8. A year range that is not a citation at all.
    doc.add_paragraph("Between (1990-2023) the trend held.")

    doc.add_paragraph("References")
    doc.add_paragraph("1. Barro RJ, Sala-i-Martin X. Convergence. J Polit Econ. 1992;100(2):223-51.")
    doc.add_paragraph("2. Marmot M. Social determinants of health. Lancet. 2005;365(9464):1099-104.")
    doc.add_paragraph("3. Theil H. Economics and Information Theory. Amsterdam: North-Holland; 1967.")
    doc.add_paragraph("4. GBD 2021 Collaborators. Global incidence. Lancet. 2024.")
    doc.add_paragraph("5. Smith AB. A fifth reference. BMJ. 2010;340:c2289.")
    doc.add_paragraph("6. Jones CD. A sixth reference. Nature. 2015;520:100-9.")
    doc.add_paragraph("7. Lee K. A seventh reference. Cell. 2018;172:1-10.")
    doc.add_paragraph("8. Park S. An eighth reference. Science. 2019;363:200-5.")
    doc.add_paragraph("9. Choi H. A ninth reference. PNAS. 2020;117:5000-10.")

    out = WEB / "fixture.docx"
    doc.save(out)
    return out


class StubCandidate:
    def __init__(self, n):
        self.year = 1990 + n
        self.corporate = "GBD 2021 Collaborators" if n == 4 else None
        self.authors = [f"surname{n}"]


class StubResolution:
    def __init__(self, n, status):
        self.status = status
        self.candidate = StubCandidate(n) if status != "REVIEW" else None


def main() -> int:
    path = build()

    doc = Document(path)
    idx = find_biblio_index(doc)
    biblio = "\n".join(p.text for p in doc.paragraphs[idx + 1:])
    raw = parse_bibliography(biblio)

    # Reference 6 is deliberately left unresolved, so the "{NEEDS REVIEW}" branch
    # is exercised alongside the live-marker one.
    resolutions = {n: StubResolution(n, "REVIEW" if n == 6 else "ACCEPTED") for n in raw}
    keys = {n: f"KEY{n:03d}" for n in raw if n != 6}

    warnings: list[str] = []
    render = make_renderer(resolutions, keys, "1234567", refs=None, warnings=warnings)
    n_marked = mark_body(doc, idx, render)
    remove_from(doc, idx)

    expect = {
        "biblio_index": idx,
        "entries": {str(k): v for k, v in raw.items()},
        "paragraphs": [p.text for p in doc.paragraphs],
        "n_marked": n_marked,
        "warnings": sorted(set(warnings)),
    }
    (WEB / "fixture.expect.json").write_text(
        json.dumps(expect, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"wrote {path.name} and fixture.expect.json")
    print(f"  bibliography at paragraph {idx}, {len(raw)} entries, {n_marked} markers")
    for w in expect["warnings"]:
        print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

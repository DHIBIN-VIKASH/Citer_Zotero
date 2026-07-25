"""Interactive confirm loop for anything the accept gate would not pass.

This is the last mile to 100%. The gate is deliberately strict, so what lands
here is genuinely ambiguous — a handful of references at most. Each decision is
written to the corrections table, so the same reference never asks twice, in this
manuscript or any future one.
"""
from __future__ import annotations

from .cache import Cache
from .models import Candidate, ParsedRef, Resolution
from .resolver import item_from_text as book_from_text


def _describe(c: Candidate) -> str:
    who = c.corporate or (", ".join(a.title() for a in c.authors[:3]) or "?")
    ident = c.doi or (f"PMID:{c.pmid}" if c.pmid else "no identifier")
    loc = f"v{c.volume or '?'}({c.issue or '?'}):{c.first_page or '?'}"
    return (f"{c.title[:88]}\n        {who} | {c.journal or c.publisher or '?'} | "
            f"{c.year or '?'} | {loc} | {ident} | {c.item_type} | {sorted(c.providers)}")


def review_loop(
    refs: dict[int, ParsedRef], results: dict[int, Resolution], cache: Cache
) -> None:
    pending = [n for n in sorted(results) if results[n].status not in ("ACCEPTED",)]
    if not pending:
        print("Nothing to review — every reference passed the accept gate.")
        return

    print(f"\n{len(pending)} reference(s) need a decision.\n")
    for n in pending:
        res, ref = results[n], refs[n]
        print("=" * 78)
        print(f"[{n}] {ref.raw}")
        if res.reason:
            print(f"  why: {res.reason}")

        options: list[Candidate] = []
        if res.candidate:
            options.append(res.candidate)
        options += [c for c in res.alternatives if c not in options][: 5 - len(options)]

        for i, c in enumerate(options, 1):
            marker = " (current pick)" if c is res.candidate else ""
            print(f"  {i}){marker} {_describe(c)}")
        if not options:
            print("  (no candidates found)")

        prompt = "  choose 1-%d / [s]kip / [d]oi / " % len(options) if options else "  [s]kip / [d]oi / "
        if ref.is_book:
            prompt += "[b]uild from reference text / "
        prompt = prompt.rstrip("/ ") + ": "

        while True:
            try:
                ans = input(prompt).strip().lower()
            except EOFError:
                print("\n  (no input available — skipping)")
                ans = "s"
            if ans in ("s", ""):
                print("  skipped — stays flagged in the document")
                break
            if ans == "b" and ref.is_book:
                cand = book_from_text(ref)
                res.candidate, res.status, res.tier, res.confidence = cand, "FROM_TEXT", "book-from-text", 0.80
                cache.put_correction(ref, cand)
                print("  accepted from reference text")
                break
            if ans == "d":
                doi = input("    DOI: ").strip()
                if doi:
                    cand = Candidate(provider="manual", title=ref.title, doi=doi,
                                     authors=list(ref.authors), corporate=ref.corporate,
                                     year=ref.year, journal=ref.journal, volume=ref.volume,
                                     issue=ref.issue, first_page=ref.first_page,
                                     last_page=ref.last_page,
                                     item_type="book" if ref.is_book else "journalArticle")
                    res.candidate, res.status, res.tier, res.confidence = cand, "ACCEPTED", "manual", 1.0
                    cache.put_correction(ref, cand)
                    print(f"  accepted {doi}")
                    break
            if ans.isdigit() and 1 <= int(ans) <= len(options):
                cand = options[int(ans) - 1]
                res.candidate, res.status, res.tier, res.confidence = cand, "ACCEPTED", "manual", 1.0
                cache.put_correction(ref, cand)
                print(f"  accepted {cand.doi or cand.title[:50]}")
                break
            print("  ?")
    print("=" * 78)

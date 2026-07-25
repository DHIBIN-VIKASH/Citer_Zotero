"""Accuracy harness: resolve the 35 real references and print a verdict table."""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from zotprep.cache import Cache
from zotprep.extractor import parse_reference
from zotprep.resolver import resolve_all

MAILTO = os.environ.get("ZOTPREP_MAILTO", "you@example.com")


async def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    raw = json.load(io.open(os.path.join(here, "refs_35.json"), encoding="utf-8"))
    refs = {i: parse_reference(i, r) for i, r in enumerate(raw, 1)}
    cache = Cache(enabled="--no-cache" not in sys.argv)

    t0 = time.time()
    done = {"n": 0}

    def progress(res):
        done["n"] += 1
        print(f"  ... {done['n']}/{len(refs)}", end="\r", file=sys.stderr)

    results = await resolve_all(refs, MAILTO, cache, workers=12, progress=progress)
    elapsed = time.time() - t0

    counts = {"ACCEPTED": 0, "FROM_TEXT": 0, "REVIEW": 0}
    print(f"\n{'#':>3} {'STATUS':<10} {'CONF':>5} {'TIER':<21} DOI / PMID")
    print("-" * 118)
    for n in sorted(results):
        r = results[n]
        counts[r.status] = counts.get(r.status, 0) + 1
        c = r.candidate
        ident = (c.doi or (f"PMID:{c.pmid}" if c and c.pmid else "-")) if c else "-"
        print(f"{n:>3} {r.status:<10} {r.confidence:>5.2f} {r.tier:<21} {ident}")
        print(f"    ref : {refs[n].title[:96]}")
        if c:
            print(f"    got : {c.title[:96]}")
            print(f"    meta: {c.journal[:48]} | {c.year} | v{c.volume} p{c.first_page} "
                  f"| {c.item_type} | {sorted(c.providers)}")
        if r.reason and r.status != "ACCEPTED":
            print(f"    why : {r.reason}")
            for alt in r.alternatives[:3]:
                print(f"      alt: {alt.year} {(alt.doi or '-')} {alt.title[:70]}")
        print()

    print("=" * 118)
    print(f"ACCEPTED {counts.get('ACCEPTED',0)} | FROM_TEXT {counts.get('FROM_TEXT',0)} "
          f"| REVIEW {counts.get('REVIEW',0)}  of {len(refs)}   ({elapsed:.1f}s)")
    out = {
        n: {
            "status": r.status, "tier": r.tier, "confidence": r.confidence,
            "doi": r.candidate.doi if r.candidate else None,
            "pmid": r.candidate.pmid if r.candidate else None,
            "got_title": r.candidate.title if r.candidate else None,
            "ref_title": refs[n].title,
        }
        for n, r in results.items()
    }
    json.dump(out, io.open(os.path.join(here, "run_35_out.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    cache.close()


if __name__ == "__main__":
    asyncio.run(main())

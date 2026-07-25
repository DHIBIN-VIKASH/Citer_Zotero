"""Command line entry point.

    python -m zotprep --manuscript yourpaper.docx --dry-run
    python -m zotprep --manuscript yourpaper.docx --live \
        --zotero-userid 1234567 --zotero-key XXXX
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys

import httpx

from .cache import Cache
from .docx_writer import (
    find_biblio_index,
    make_renderer,
    mark_body,
    parse_bibliography,
    remove_from,
)
from .extractor import parse_reference
from .resolver import USER_AGENT, resolve_all
from .zotero.client import ZoteroWriter, enrich, to_item


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="zotprep", description="Resolve manuscript references and prepare a Zotero-linked .docx")
    ap.add_argument("--manuscript", required=True, help=".docx containing the manuscript")
    ap.add_argument("--bibliography", help="separate bibliography file; else auto-split on 'References'")
    ap.add_argument("--outdir", default="zot_out")
    ap.add_argument("--mailto", default=os.environ.get("ZOTPREP_MAILTO", "you@example.com"),
                    help="contact email for the Crossref/NCBI polite pools")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve and report, but create nothing in Zotero")
    ap.add_argument("--live", action="store_true",
                    help="create/reuse items in your Zotero library (must be explicit)")
    ap.add_argument("--zotero-userid", default=os.environ.get("ZOTERO_USERID"),
                    help="numeric Zotero userID; defaults to $ZOTERO_USERID")
    ap.add_argument("--zotero-key", default=os.environ.get("ZOTERO_KEY"),
                    help="read/write Zotero API key; defaults to $ZOTERO_KEY")
    ap.add_argument("--review", action="store_true",
                    help="prompt for anything not auto-accepted instead of leaving it flagged")
    return ap


async def run(args) -> int:
    from docx import Document

    # Writing to someone's Zotero library is the one irreversible thing this tool
    # does, so it must be asked for explicitly — never the result of a forgotten
    # flag. --dry-run stays available as the obvious safe default.
    if args.live and args.dry_run:
        sys.exit("--live and --dry-run are mutually exclusive.")
    if not args.live:
        args.dry_run = True
    if args.live and not (args.zotero_userid and args.zotero_key):
        sys.exit(
            "--live needs a Zotero userID and API key.\n"
            "  Pass --zotero-userid/--zotero-key, or set ZOTERO_USERID and ZOTERO_KEY.\n"
            "  Get both at https://www.zotero.org/settings/keys (key needs write access)."
        )

    os.makedirs(args.outdir, exist_ok=True)
    doc = Document(args.manuscript)

    if args.bibliography:
        biblio_text = open(args.bibliography, encoding="utf-8").read()
        biblio_idx = len(doc.paragraphs)
    else:
        biblio_idx = find_biblio_index(doc)
        if biblio_idx is None:
            sys.exit("No 'References' heading found; pass --bibliography.")
        biblio_text = "\n".join(p.text for p in doc.paragraphs[biblio_idx + 1:])

    raw_refs = parse_bibliography(biblio_text)
    refs = {n: parse_reference(n, t) for n, t in raw_refs.items()}
    print(f"Parsed {len(refs)} references.")

    cache = Cache(enabled=not args.no_cache)
    done = {"n": 0}

    def progress(_res):
        done["n"] += 1
        print(f"  resolving {done['n']}/{len(refs)}", end="\r", file=sys.stderr)

    results = await resolve_all(refs, args.mailto, cache, workers=args.workers, progress=progress)
    print()

    if args.review:
        from .review import review_loop

        review_loop(refs, results, cache)

    accepted = [n for n in sorted(results) if results[n].status in ("ACCEPTED", "FROM_TEXT")]
    unresolved = [n for n in sorted(results) if n not in accepted]

    # upgrade winning candidates to canonical Crossref records for item quality
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=30.0,
                                 follow_redirects=True) as client:
        for n in accepted:
            res = results[n]
            if res.candidate:
                res.candidate = await enrich(client, res.candidate, args.mailto)

    writer = ZoteroWriter(args.zotero_userid or "0", args.zotero_key or "", dry_run=args.dry_run)
    if not args.dry_run:
        print("Indexing existing Zotero library for duplicates ...")
        writer.load_library()
    items = [(n, to_item(results[n].candidate, refs[n])) for n in accepted if results[n].candidate]
    keys = writer.create(items)
    for n, k in keys.items():
        results[n].zotero_key = k
    print(f"Zotero items ready: {len(keys)} ({'dry run' if args.dry_run else 'created/reused'})")

    warnings: list[str] = []
    render = make_renderer(results, keys, args.zotero_userid or "0", refs=refs,
                           warnings=warnings)
    n_marked = mark_body(doc, biblio_idx, render)
    if not args.bibliography:
        remove_from(doc, biblio_idx)
    out_docx = os.path.join(args.outdir, "manuscript_scannable.docx")
    doc.save(out_docx)

    report = os.path.join(args.outdir, "report.csv")
    with open(report, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["n", "status", "tier", "confidence", "doi", "pmid", "zotero_key",
                    "resolved_title", "reason", "advisory", "raw_reference"])
        for n in sorted(results):
            r = results[n]
            c = r.candidate
            w.writerow([n, r.status, r.tier, r.confidence, (c.doi if c else ""),
                        (c.pmid if c else ""), r.zotero_key or "",
                        (c.title if c else ""), r.reason,
                        " | ".join(r.notes), refs[n].raw])

    print(f"\nWrote {out_docx}")
    print(f"Wrote {report}   ({n_marked} in-text citations rewritten)")
    advisories = [(n, note) for n in sorted(results) for note in results[n].notes]
    if advisories:
        print("\n*** Bibliography entries to verify "
              "(resolved, but the entry's own fields disagree):")
        for n, note in advisories:
            print(f"  [{n}] {note}")
    if warnings:
        print("\n*** In-text citation problems (document left unchanged at these spots):")
        for w in dict.fromkeys(warnings):
            print(f"  - {w}")
    if unresolved:
        print(f"\n*** {len(unresolved)} reference(s) unresolved: {unresolved}")
        print("Marked '{NEEDS REVIEW: n}' in the document. Re-run with --review to fix interactively.")
    else:
        print("\nAll references resolved. Next: Zotero > Tools > ODF Scan on the output .docx")
    cache.close()
    return 1 if unresolved else 0


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()

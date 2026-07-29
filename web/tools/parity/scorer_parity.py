"""Differential test: web/src/scorer.js vs zotprep/scorer.py.

This is the layer that decides, so the comparison covers every observable, not
just the final verdict: each Signals field, the derived `locator_ok`/`year_ok`,
the human-readable `failing()` strings that end up in the report, the ranking
confidence, the accept tuple, and the full ordering produced by `rank()`
including its tie-breaks.

Candidates are generated from the real references by *perturbing* them, one
signal at a time, so the bank straddles every gate rather than sitting safely
inside or outside it: titles just above and just below 0.88/0.90/0.92/0.93,
years off by exactly 1 and 2, volumes and pages that agree, disagree or are
absent, journals in abbreviated and full form, book vs journal item types, and
provider sets of size 1, 2 and 3 with and without the ecitmatch tag.

Run:  python web/tools/parity/scorer_parity.py
"""
from __future__ import annotations

import argparse
import json
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from harness import ROOT, run_node  # noqa: E402

from zotprep.extractor import parse_reference  # noqa: E402
from zotprep.models import Candidate, ParsedRef  # noqa: E402
from zotprep.scorer import accept, confidence, rank, signals  # noqa: E402

REF_FIELDS = [
    "n", "raw", "doi", "pmid", "pmcid", "authors", "corporate", "title",
    "journal", "year", "volume", "issue", "first_page", "last_page",
    "is_book", "is_chapter", "book_title", "publisher", "place", "edition",
]
CAND_FIELDS = [
    "provider", "title", "doi", "pmid", "authors", "corporate", "year",
    "journal", "journal_abbrev", "volume", "issue", "first_page", "last_page",
    "item_type", "book_title", "publisher", "place",
]
SIGNAL_FIELDS = [
    "title_sim", "year_delta", "author_ok", "author_overlap", "corporate_ok",
    "journal_ok", "volume_ok", "first_page_ok", "last_page_ok", "type_ok",
    "n_providers", "ecitmatch",
]


def bits(x: float) -> str:
    return struct.pack(">d", float(x)).hex()


def dump_ref(r: ParsedRef) -> dict:
    return {f: getattr(r, f) for f in REF_FIELDS}


def dump_cand(c: Candidate) -> dict:
    d = {f: getattr(c, f) for f in CAND_FIELDS}
    d["providers"] = sorted(c.providers or {c.provider})
    return d


def dump_signals(s) -> dict:
    out = {}
    for f in SIGNAL_FIELDS:
        v = getattr(s, f)
        out[f] = bits(v) if isinstance(v, float) or (isinstance(v, int) and not isinstance(v, bool)) else v
    # year_delta and n_providers are ints in Python; keep the same treatment as
    # the runner, which bit-encodes every JS number
    out["locator_ok"] = s.locator_ok
    out["year_ok"] = s.year_ok
    out["failing"] = s.failing()
    return out


# --- candidate generation -----------------------------------------------------

TITLE_EDITS = [
    ("identical", lambda t: t),
    ("case", lambda t: t.upper()),
    ("expanded", lambda t: t + ": a systematic analysis for the Global Burden of Disease Study"),
    ("truncated", lambda t: " ".join(t.split()[: max(1, len(t.split()) // 2)])),
    ("one-word-dropped", lambda t: " ".join(t.split()[:-1]) if len(t.split()) > 1 else t),
    ("one-word-changed", lambda t: " ".join((["different"] + t.split()[1:]) if t.split() else [t])),
    ("subset-of-other", lambda t: "Mitochondrial dysfunction and mitophagy blockade contribute to " + t),
    ("unrelated", lambda _t: "An entirely unrelated paper about something else"),
    ("empty", lambda _t: ""),
    ("punctuation", lambda t: t.replace(" ", "-")),
]

JOURNAL_EDITS = [
    ("same", lambda j: j),
    ("full-ish", lambda j: "Journal of " + j),
    ("wrong", lambda _j: "Some Other Journal"),
    ("empty", lambda _j: ""),
]


def build_pairs(seed: int):
    rng = random.Random(seed)
    corpus = ROOT / "tests" / "refs_35.json"
    raws = json.loads(corpus.read_text(encoding="utf-8")) if corpus.exists() else []
    raws += [
        "1. Barro RJ, Sala-i-Martin X. Convergence. J Polit Econ. 1992;100(2):223-51.",
        "2. Theil H. Economics and Information Theory. Amsterdam: North-Holland; 1967.",
        "3. GBD 2021 Low Back Pain Collaborators. Global burden. Lancet Rheumatol. 2023;5(6):e316-29.",
        "4. Author A. Chapter. In: Ed B, eds. Book. Amsterdam: Elsevier, 2013: 1113-36.",
    ]
    refs = [parse_reference(i + 1, r) for i, r in enumerate(raws)]

    pairs = []
    for ref in refs:
        base_title = ref.title or "A title"
        for tname, tedit in TITLE_EDITS:
            for jname, jedit in JOURNAL_EDITS:
                for dy in (0, 1, 2, None):
                    for locator in ("exact", "vol-only", "page-only", "none", "wrong"):
                        for provs in (
                            ["crossref"],
                            ["crossref", "openalex"],
                            ["crossref", "openalex", "pubmed"],
                            ["pubmed:ecitmatch"],
                            ["pubmed:ecitmatch", "crossref"],
                        ):
                            for itype in ("journalArticle", "book", "bookSection"):
                                if rng.random() > 0.02:
                                    continue  # sample the cross product
                                c = Candidate(
                                    provider=provs[0],
                                    title=tedit(base_title),
                                    doi=rng.choice([None, "10.1234/abc", "10.2307/2109990"]),
                                    pmid=rng.choice([None, "12345678"]),
                                    authors=list(ref.authors) if rng.random() < 0.7 else ["Nobody"],
                                    corporate=ref.corporate if rng.random() < 0.5 else None,
                                    year=(None if dy is None else (ref.year or 2000) + dy),
                                    journal=jedit(ref.journal or "Lancet"),
                                    journal_abbrev=ref.journal or "",
                                    volume=(ref.volume if locator in ("exact", "vol-only")
                                            else ("999" if locator == "wrong" else None)),
                                    first_page=(ref.first_page if locator in ("exact", "page-only")
                                                else ("999" if locator == "wrong" else None)),
                                    last_page=(ref.last_page if locator == "exact" else None),
                                    item_type=itype,
                                    providers=set(provs),
                                )
                                pairs.append({"ref": dump_ref(ref), "cand": dump_cand(c),
                                              "_ref": ref, "_cand": c,
                                              "_label": f"{tname}/{jname}/dy={dy}/{locator}/{itype}"})

    # deterministic extras that pin the exact gate boundaries
    for ref in refs[:8]:
        for sim_target in ("just-under-88", "just-over-88", "just-under-90", "just-over-90",
                           "just-under-92", "just-over-92", "just-under-93", "just-over-93"):
            toks = (ref.title or "a b c d e f g h i j").split()
            n = len(toks)
            drop = {"just-under-88": max(1, n // 6), "just-over-88": max(1, n // 9),
                    "just-under-90": max(1, n // 7), "just-over-90": max(1, n // 10),
                    "just-under-92": max(1, n // 8), "just-over-92": max(1, n // 12),
                    "just-under-93": max(1, n // 9), "just-over-93": max(1, n // 14)}[sim_target]
            c = Candidate(
                provider="crossref",
                title=" ".join(toks[: max(1, n - drop)]),
                authors=list(ref.authors),
                corporate=ref.corporate,
                year=ref.year,
                journal=ref.journal,
                journal_abbrev=ref.journal,
                volume=ref.volume,
                first_page=ref.first_page,
                last_page=ref.last_page,
                item_type="journalArticle",
                providers={"crossref", "openalex"},
            )
            pairs.append({"ref": dump_ref(ref), "cand": dump_cand(c),
                          "_ref": ref, "_cand": c, "_label": sim_target})

    # --- cases constructed to hit one exact boundary each --------------------
    # Random perturbation lands *near* the gates but essentially never *on*
    # them, and never on a float tie. Mutation testing showed the bank above
    # giving a clean pass to a port with a shifted threshold, an uncapped bonus
    # and Python-incompatible float printing, so these are built by arithmetic
    # rather than by sampling.
    #
    # ratio() is (1 - dist/lensum)*100 with dist = len1+len2-2*lcs, so choosing
    # two 8-character tokens with a known LCS fixes the similarity exactly.
    def pair_with_similarity(lcs_len, tok_len=8):
        a = "abcdefghijklmnop"[:tok_len]
        b = a[:lcs_len] + ("z" * (tok_len - lcs_len))
        return a, b

    def make(ref, **kw):
        base = dict(
            provider="crossref", authors=list(ref.authors), corporate=ref.corporate,
            year=ref.year, journal=ref.journal, journal_abbrev=ref.journal,
            volume=ref.volume, first_page=ref.first_page, last_page=ref.last_page,
            item_type="journalArticle", providers={"crossref"},
        )
        base.update(kw)
        return Candidate(**base)

    ref0 = refs[0]

    # Float ties in failing()'s "title similarity %.2f". Python rounds half to
    # even, JavaScript's toFixed rounds half away from zero: 0.125 formats as
    # "0.12" and "0.13" respectively, and 0.625 as "0.62" and "0.63". Both are
    # exactly representable and both are reachable.
    for lcs_len, label in ((1, "sim-0.125-tie"), (5, "sim-0.625-tie"),
                           (3, "sim-0.375-tie"), (7, "sim-0.875-tie")):
        a, b = pair_with_similarity(lcs_len)
        r = ParsedRef(n=900 + lcs_len, raw=a)
        r.title = a
        r.journal = ref0.journal
        r.year = ref0.year
        r.authors = list(ref0.authors)
        pairs.append({"ref": dump_ref(r), "cand": dump_cand(make(r, title=b)),
                      "_ref": r, "_cand": make(r, title=b), "_label": label})

    # Exactly 0.90 and exactly 0.88: the fingerprint and provider-agreement
    # gates use >=, so a port using > accepts nothing here.
    for tok_len, lcs_len, provs, label in (
        (10, 9, {"crossref"}, "sim-exactly-0.90-1-provider"),
        (10, 9, {"crossref", "openalex"}, "sim-exactly-0.90-2-providers"),
        (25, 22, {"crossref", "openalex"}, "sim-exactly-0.88-2-providers"),
    ):
        a, b = pair_with_similarity(lcs_len, tok_len)
        r = ParsedRef(n=910, raw=a)
        r.title, r.journal, r.year = a, ref0.journal, ref0.year
        r.authors = list(ref0.authors)
        r.volume, r.first_page, r.last_page = ref0.volume, ref0.first_page, ref0.last_page
        c = make(r, title=b, providers=provs)
        pairs.append({"ref": dump_ref(r), "cand": dump_cand(c), "_ref": r, "_cand": c,
                      "_label": label})

    # Four or more independent providers, where min(0.05, ...) actually binds.
    for provs, label in (
        ({"crossref", "openalex", "europepmc", "pubmed"}, "4-providers"),
        ({"crossref", "openalex", "europepmc", "pubmed", "semanticscholar"}, "5-providers"),
        # ... and provider names sharing a prefix, where the count must collapse
        # them: {"pubmed", "pubmed:ecitmatch"} is ONE provider, not two.
        ({"pubmed", "pubmed:ecitmatch"}, "prefix-collision-pubmed"),
        ({"crossref", "crossref:canonical"}, "prefix-collision-crossref"),
        ({"crossref", "crossref:canonical", "openalex"}, "prefix-collision-plus-one"),
        ({"pubmed", "pubmed:ecitmatch", "crossref", "crossref:canonical"}, "two-collisions"),
    ):
        c = make(ref0, title=ref0.title, providers=provs)
        pairs.append({"ref": dump_ref(ref0), "cand": dump_cand(c), "_ref": ref0, "_cand": c,
                      "_label": label})

    # Consortium keys where a tail word appears twice: str.replace removes every
    # occurrence, String.replace removes one.
    for corporate, cand_title, label in (
        ("Global Group Burden Group", "global burden of disease estimates", "repeat-tail-group"),
        ("Network Study Network", "study of something", "repeat-tail-network"),
        ("GBD Collaborators 2021 Collaborators", "gbd 2021 estimates", "repeat-tail-collaborators"),
        ("Committee Consortium Committee", "consortium report", "repeat-tail-committee"),
    ):
        r = ParsedRef(n=920, raw=corporate)
        r.title, r.journal, r.year = cand_title, ref0.journal, ref0.year
        r.corporate = corporate
        c = make(r, title=cand_title, corporate=None, authors=[])
        pairs.append({"ref": dump_ref(r), "cand": dump_cand(c), "_ref": r, "_cand": c,
                      "_label": label})

    # Corporate similarity landing between 0.70 and 0.80, where the gate's
    # threshold is the only thing deciding, with a title that cannot rescue it
    # through the substring probe.
    for lcs_len, label in ((11, "corp-sim-just-under-0.80"), (12, "corp-sim-around-0.80"),
                           (10, "corp-sim-0.71"), (13, "corp-sim-0.87")):
        a, b = pair_with_similarity(lcs_len, 15)
        r = ParsedRef(n=930 + lcs_len, raw=a)
        r.title, r.journal, r.year = "some unrelated title", ref0.journal, ref0.year
        r.corporate = a
        c = make(r, title="an entirely different heading", corporate=b, authors=[])
        pairs.append({"ref": dump_ref(r), "cand": dump_cand(c), "_ref": r, "_cand": c,
                      "_label": label})

    # ranking groups: several candidates per reference, including exact ties
    rankings = []
    for ref in refs:
        cands = []
        for i in range(6):
            cands.append(Candidate(
                provider=["crossref", "openalex", "europepmc", "pubmed", "s2", "crossref"][i],
                title=(ref.title or "t") if i % 2 == 0 else " ".join((ref.title or "t").split()[:-1]),
                doi=[None, "10.1234/a", "10.2307/1", "10.1234/b", None, "10.1234/a"][i],
                pmid=[None, "1", None, "2", None, None][i],
                authors=list(ref.authors),
                year=ref.year,
                journal=ref.journal,
                journal_abbrev=ref.journal,
                volume=ref.volume if i < 4 else None,
                first_page=ref.first_page if i < 3 else None,
                last_page=ref.last_page if i < 2 else None,
                item_type="journalArticle",
                providers={"crossref"} if i < 2 else {"crossref", "openalex"},
            ))
        rankings.append({"ref": dump_ref(ref), "cands": [dump_cand(c) for c in cands],
                         "_ref": ref, "_cands": cands})
    return pairs, rankings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260729)
    ap.add_argument("--show", type=int, default=8)
    args = ap.parse_args()

    pairs, rankings = build_pairs(args.seed)

    want = []
    for p in pairs:
        s = signals(p["_ref"], p["_cand"])
        ok, tier, conf = accept(p["_ref"], p["_cand"], s)
        want.append({
            "signals": dump_signals(s),
            "confidence": bits(confidence(s)),
            "accept": [ok, tier, bits(conf)],
        })

    want_rank = []
    for r in rankings:
        order = rank(r["_ref"], r["_cands"])
        idx = {id(c): i for i, c in enumerate(r["_cands"])}
        want_rank.append([[idx[id(c)], bits(cf)] for c, _s, cf in order])

    got = run_node("scorer_runner.mjs", {
        "pairs": [{"ref": p["ref"], "cand": p["cand"]} for p in pairs],
        "rankings": [{"ref": r["ref"], "cands": r["cands"]} for r in rankings],
    })

    failures = []
    for p, w, g in zip(pairs, want, got["scored"]):
        for f in list(SIGNAL_FIELDS) + ["locator_ok", "year_ok", "failing"]:
            if w["signals"][f] != g["signals"].get(f):
                failures.append((p["_label"], f"signals.{f}", w["signals"][f], g["signals"].get(f)))
        if w["confidence"] != g["confidence"]:
            failures.append((p["_label"], "confidence",
                             struct.unpack(">d", bytes.fromhex(w["confidence"]))[0],
                             struct.unpack(">d", bytes.fromhex(g["confidence"]))[0]))
        if w["accept"] != g["accept"]:
            failures.append((p["_label"], "accept", w["accept"], g["accept"]))

    for i, (w, g) in enumerate(zip(want_rank, got["ranked"])):
        if w != g:
            failures.append((f"ranking[{i}]", "rank order", w, g))

    n_obs = len(pairs) * (len(SIGNAL_FIELDS) + 4) + len(rankings)
    if not failures:
        print(f"PASS  scorer: {len(pairs)} ref/candidate pairs + {len(rankings)} rankings, "
              f"{n_obs} observations, 0 mismatches")
        return 0

    print(f"FAIL  scorer: {len(failures)}/{n_obs} mismatches")
    by: dict[str, int] = {}
    for _, f, _, _ in failures:
        by[f] = by.get(f, 0) + 1
    for f, n in sorted(by.items(), key=lambda kv: -kv[1]):
        print(f"    {f:<24} {n}")
    for label, f, w, g in failures[: args.show]:
        print(f"\n  {f}   [{label}]")
        print(f"    python = {w!r}")
        print(f"    js     = {g!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Differential test: web/src/fuzz.js vs the real rapidfuzz.

The browser port is only trustworthy if its string metric agrees with the one
the verified Python engine was tuned against. "Agrees" here means the identical
IEEE-754 double, not a value within some tolerance: scorer.py compares title
similarity against hard thresholds (0.88/0.90/0.92/0.93), so a last-bit
disagreement is enough to flip an ACCEPTED reference into REVIEW.

Cases are drawn from three pools, because each catches a different class of bug:

  real       — actual bibliography titles, normalized exactly as the resolver
               normalizes them. This is the distribution that matters.
  mutated    — real titles with tokens dropped, duplicated, reordered, truncated
               and mis-spelled. Exercises the subset/superset paths in
               token_set_ratio that plain corpus pairs rarely reach.
  adversarial— empties, single characters, repeated tokens, unicode beyond
               ASCII. Catches Python-vs-JS differences in split/sort semantics.

Run:  python web/tools/parity/fuzz_parity.py [--cases N]
"""
from __future__ import annotations

import argparse
import json
import random
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from rapidfuzz import fuzz  # noqa: E402

from zotprep.utils import norm_text  # noqa: E402

RUNNER = Path(__file__).with_name("fuzz_runner.mjs")

ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def bits(x: float) -> str:
    return struct.pack(">d", x).hex()


def real_titles() -> list[str]:
    """Normalized titles from the checked-in reference corpus."""
    out: list[str] = []
    corpus = ROOT / "tests" / "refs_35.json"
    if corpus.exists():
        raw = json.loads(corpus.read_text(encoding="utf-8"))
        for entry in raw:
            out.append(norm_text(entry))
            # the title alone, roughly: middle sentence of the reference
            parts = [p.strip() for p in entry.split(".") if p.strip()]
            out.extend(norm_text(p) for p in parts if len(p.split()) >= 3)
    return [t for t in out if t]


def mutate(title: str, rng: random.Random) -> str:
    toks = title.split()
    if not toks:
        return title
    op = rng.randrange(7)
    if op == 0:  # drop a token
        toks.pop(rng.randrange(len(toks)))
    elif op == 1:  # duplicate a token
        toks.insert(rng.randrange(len(toks) + 1), rng.choice(toks))
    elif op == 2:  # shuffle
        rng.shuffle(toks)
    elif op == 3:  # truncate (bibliography short form vs version of record)
        toks = toks[: max(1, len(toks) // 2)]
    elif op == 4:  # append unrelated tail
        toks += [
            "a", "systematic", "analysis", "for", "the", "global", "burden",
            "of", "disease", "study", "2021",
        ]
    elif op == 5:  # typo inside one token
        i = rng.randrange(len(toks))
        w = toks[i]
        if w:
            j = rng.randrange(len(w))
            toks[i] = w[:j] + rng.choice(ALPHABET) + w[j + 1:]
    else:  # swap two adjacent tokens
        if len(toks) > 1:
            i = rng.randrange(len(toks) - 1)
            toks[i], toks[i + 1] = toks[i + 1], toks[i]
    return " ".join(toks)


def adversarial() -> list[tuple[str, str]]:
    odd = [
        "", " ", "  \t\n ", "a", "aa", "a a", "a  a", "a a a",
        "abc abd", "the the the", "0 1 2 3", "z", "zz zz zz",
        # non-ASCII: exercises code-point vs UTF-16 sort order and NFC handling
        "é", "é a", "ß s", "über uber", "北京 大学", "𝒜 a", "\U0001f600 x",
        # Token sorting must follow code point order (Python `sorted`), not
        # UTF-16 code unit order (JavaScript's default `Array.sort`). The two
        # disagree exactly when an astral character meets a BMP character above
        # U+D800: "𝒜" is U+1D49C but its first code unit is U+D835, so it sorts
        # *before* "Ａ" (U+FF21) in UTF-16 and *after* it by code point.
        "\U0001d49c Ａ", "Ａ \U0001d49c", "\U0001d49c Ａ ﬁ",
        "\U0001f600 Ａ", "￮ \U0001d7ce", "\U0001d7ce ￮ 龥",
        "\U0001d49c", "Ａ", "\U00020000 ～", "～ \U00020000",
        # long repeated content, to stress the LCS DP
        " ".join(["token"] * 40), " ".join(["token"] * 39 + ["other"]),
    ]
    pairs = [(a, b) for a in odd for b in odd]
    return pairs


def build_pairs(n: int, seed: int) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    titles = real_titles()
    pairs: list[tuple[str, str]] = list(adversarial())

    if titles:
        # every real title against itself and against a near neighbour
        for t in titles:
            pairs.append((t, t))
        while len(pairs) < n:
            a = rng.choice(titles)
            roll = rng.random()
            if roll < 0.45:
                b = mutate(a, rng)
            elif roll < 0.65:
                b = mutate(mutate(a, rng), rng)
            else:
                b = rng.choice(titles)
            pairs.append((a, b))

    # purely random token soup, to hit paths the corpus never reaches
    while len(pairs) < n + 2000:
        def soup() -> str:
            k = rng.randrange(0, 12)
            return " ".join(
                "".join(rng.choice(ALPHABET) for _ in range(rng.randrange(1, 9)))
                for _ in range(k)
            )
        pairs.append((soup(), soup()))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20260729)
    ap.add_argument("--show", type=int, default=10, help="max mismatches to print")
    args = ap.parse_args()

    pairs = build_pairs(args.cases, args.seed)
    print(f"fuzz parity: {len(pairs)} pairs")

    proc = subprocess.run(
        ["node", str(RUNNER)],
        input=json.dumps({"pairs": [list(p) for p in pairs]}),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return 2
    js = json.loads(proc.stdout)

    funcs = {
        "ratio": fuzz.ratio,
        "token_sort_ratio": fuzz.token_sort_ratio,
        "token_set_ratio": fuzz.token_set_ratio,
    }

    mismatches: list[tuple] = []
    for (a, b), got in zip(pairs, js):
        for name, fn in funcs.items():
            want = bits(fn(a, b))
            if want != got[name]:
                mismatches.append(
                    (name, a, b, struct.unpack(">d", bytes.fromhex(want))[0],
                     struct.unpack(">d", bytes.fromhex(got[name]))[0])
                )

    total = len(pairs) * len(funcs)
    if not mismatches:
        print(f"PASS  {total} comparisons, 0 mismatches (exact IEEE-754 equality)")
        return 0

    print(f"FAIL  {len(mismatches)}/{total} mismatches")

    by_func: dict[str, int] = {}
    for name, *_ in mismatches:
        by_func[name] = by_func.get(name, 0) + 1
    for name in funcs:
        print(f"    {name:<18} {by_func.get(name, 0)}")

    def show(s: str) -> str:
        # the harness must stay readable on a cp1252 console
        return ascii(s)

    for name, a, b, want, got in mismatches[: args.show]:
        print(f"\n  {name}")
        print(f"    a      = {show(a)}")
        print(f"    b      = {show(b)}")
        print(f"    python = {want!r}")
        print(f"    js     = {got!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

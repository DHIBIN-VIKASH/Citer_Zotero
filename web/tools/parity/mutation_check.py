"""Mutation testing for the parity harnesses.

A differential test that passes proves nothing unless it can also fail. This
tool injects a bank of realistic port mistakes into the JavaScript sources, one
at a time, and requires the corresponding parity harness to catch each one. A
mutant that survives is a hole in the harness, not a harmless difference — the
two utils holes found this way (leading-side `str.strip`, and the `split(":")`
journal variant) were both real blind spots that a green harness was hiding.

Every mutation is a mistake a careful person would plausibly make porting Python
to JavaScript: the ASCII-vs-Unicode character classes, `str.replace` replacing
one occurrence instead of all, `trim()` standing in for `strip(chars)`, integer
vs float division, `||` where the original means `is None`.

A few mutants are *equivalent*: the edit changes the source but provably cannot
change the result, so no test can kill it and demanding one would be chasing a
ghost. Those are listed with the argument for why, and are expected to survive —
if one ever starts failing, the assumption behind it has broken and the note is
the thing to re-check. Everything else must be killed.

The source file is always restored, including when a harness crashes.

Run:  python web/tools/parity/mutation_check.py [module ...]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SRC = ROOT / "web" / "src"

# Mutants that cannot be killed because the edit cannot change any result.
# Keyed by label; the value is the argument for equivalence.
EQUIVALENT: dict[str, str] = {
    "identical-title short circuit removed":
        "When normText(a) == normText(b) and is non-empty, token_set_ratio takes "
        "its subset short-circuit and returns 100, and token_sort_ratio compares "
        "a string with itself and also returns 100. max(100,100)/100 is exactly "
        "the 1.0 the short circuit returns, so it is an optimisation only. The "
        "empty case never reaches it — it is caught by the `!na || !nb` guard above.",
    "greedy volume in the locator":
        "The volume class [\\dA-Za-z] excludes both '(' and ':', which are the only "
        "characters that can follow it, so lazy and greedy quantifiers are forced "
        "to the same maximal alphanumeric run; when that run exceeds the bound both "
        "fail identically. 400,000 randomised locator strings produced no input "
        "where the two patterns disagree on any captured group.",
    "rstrip removes a single dot only":
        "The value is passed through stripChars(..., ' .,;') a few lines later, "
        "which removes every remaining trailing dot. Removing one dot instead of "
        "all of them therefore cannot survive to the returned field.",
    "|| instead of ?? when a year is already set":
        "ref.year is either null or a year matched by (?:19|20)\\d{2}, so it is "
        "never 0 or NaN — the only values where || and ?? differ.",
    "confidence terms reordered (float associativity)":
        "The rewrite is `0.0 + (0.42 * title_sim)` instead of `0.42 * title_sim`. "
        "Adding positive zero to a finite double is the identity in IEEE-754, and "
        "0.42*title_sim is never -0 because title_sim is non-negative, so no "
        "reachable input can distinguish them. Reordering any *later* term would "
        "not be equivalent — those mutants are killed.",
    "corporate fallback when no surnames found removed":
        "The line is unreachable. Reaching it needs looksLikeAuthors() to be true, "
        "which requires at least one chunk to match AUTHOR_TOKEN; parseAuthors "
        "then iterates the same chunks with the same pattern and pushes a surname "
        "for each match, so `surnames` cannot be empty at that point.",
}

# (label, find, replace) — `find` must appear verbatim in the source.
PROBES: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "fuzz": ("fuzz.js", [
        (
            "ratio uses 100-100d/l instead of (1-d/l)*100",
            "return (1.0 - dist / lensum) * 100.0;",
            "return 100.0 - (100.0 * dist) / lensum;",
        ),
        (
            "token_set uses (1-d/l)*100 instead of 100-100d/l",
            "return lensum ? 100.0 - (100.0 * dist) / lensum : 100.0;",
            "return lensum ? (1.0 - dist / lensum) * 100.0 : 100.0;",
        ),
        (
            "empty token sets score 100 instead of 0",
            "if (!ta.size || !tb.size) return 0.0;",
            "if (!ta.size || !tb.size) return 100.0;",
        ),
        (
            "no subset short-circuit to 100",
            "if (intersect.length && (!diffAB.length || !diffBA.length)) return 100.0;",
            "",
        ),
        (
            "UTF-16 length instead of code points",
            "const abLen = cpLength(abJoined);",
            "const abLen = abJoined.length;",
        ),
        (
            "UTF-16 sort instead of code point sort",
            "const sa = splitTokens(a).sort(byCodePoint).join(' ');",
            "const sa = splitTokens(a).sort().join(' ');",
        ),
        (
            "sect ratios dropped from the max",
            "if (sectAbRatio > result) result = sectAbRatio;",
            "",
        ),
    ]),
    "utils": ("utils.js", [
        (
            "leading strip disabled",
            "while (start < end && chars.includes(s[start])) start++;",
            "while (start < end && chars.includes(s[start]) && chars !== ' .,;') start++;",
        ),
        (
            "trailing strip disabled",
            "while (end > start && chars.includes(s[end - 1])) end--;",
            "while (end > start && chars.includes(s[end - 1]) && chars !== ' .,;') end--;",
        ),
        (
            "ASCII lowercase test in surnameOf",
            "/^\\p{Ll}/u.test(parts[i - 1].slice(0, 1))",
            "/^[a-z]/.test(parts[i - 1].slice(0, 1))",
        ),
        (
            "NFC instead of NFKC in normText",
            "String(s ?? '').normalize('NFKC')",
            "String(s ?? '').normalize('NFC')",
        ),
        (
            "token_sort dropped from titleSimilarity",
            "Math.max(tokenSetRatio(na, nb), tokenSortRatio(na, nb))",
            "tokenSetRatio(na, nb)",
        ),
        (
            "journal token-count equality relaxed",
            "if (candToks.length !== refToks.length) continue;",
            "if (candToks.length < refToks.length) continue;",
        ),
        (
            "post-colon journal variant dropped",
            "if (cand.includes(':')) variants.push(cand.split(':')[0]);",
            "",
        ),
        (
            "pageEqual keeps punctuation",
            "stripAccents(x).toLowerCase().replace(/[^a-z0-9]/g, '')",
            "stripAccents(x).toLowerCase()",
        ),
        (
            "normDoi keeps the trailing dot",
            "d = rstripChars(d, '.');",
            "",
        ),
        (
            "title stopwords not dropped",
            "!TITLE_STOPWORDS.has(x)",
            "true",
        ),
        (
            "journal prefix test one-directional",
            "candToks[i].startsWith(r) || r.startsWith(candToks[i])",
            "candToks[i].startsWith(r)",
        ),
        (
            "identical-title short circuit removed",
            "if (na === nb) return 1.0;",
            "",
        ),
    ]),
    "extractor": ("extractor.js", [
        (
            "DOI stripped only once, not globally",
            "text = text.split(m[0]).join(' ');",
            "text = text.replace(m[0], ' ');",
        ),
        (
            "ASCII word boundary for PMID",
            "const PMID_RE = new RegExp(`${NWB}PMID:?\\\\s*(\\\\d{4,9})${NWA}`, 'iu');",
            "const PMID_RE = /\\bPMID:?\\s*(\\d{4,9})\\b/i;",
        ),
        (
            "ASCII \\w in the author token",
            "[\\\\p{L}\\\\p{N}_'’-]*(?:\\\\s+[a-z]{2,3})?",
            "[\\\\w'’-]*(?:\\\\s+[a-z]{2,3})?",
        ),
        (
            "ASCII uppercase test for APA surnames",
            "/^\\p{Lu}/u.test(word.slice(0, 1))",
            "/^[A-Z]/.test(word.slice(0, 1))",
        ),
        # The same normalise-and-strip line appears at both exits of
        # parse_reference. They are probed separately because a single
        # replacement would silently only ever hit the first, which is how an
        # earlier run reported a survivor that was really untested coverage of
        # the book-chapter branch.
        (
            "trim() instead of strip(' .,;') on title (chapter exit)",
            "      ref.title = stripChars(ref.title.replace(/\\s+/g, ' '), ' .,;');\n      return ref;",
            "      ref.title = ref.title.replace(/\\s+/g, ' ').trim();\n      return ref;",
        ),
        (
            "trim() instead of strip(' .,;') on title (main exit)",
            "  ref.title = stripChars(ref.title.replace(/\\s+/g, ' '), ' .,;');\n  ref.journal",
            "  ref.title = ref.title.replace(/\\s+/g, ' ').trim();\n  ref.journal",
        ),
        (
            "trim() instead of strip(' .,;') on journal",
            "  ref.journal = stripChars(ref.journal.replace(/\\s+/g, ' '), ' .,;');",
            "  ref.journal = ref.journal.replace(/\\s+/g, ' ').trim();",
        ),
        (
            "float division in the author-list vote",
            "Math.max(1, Math.floor(chunks.length / 2))",
            "Math.max(1, chunks.length / 2)",
        ),
        (
            "greedy volume in the locator",
            "'(?<vol>[A-Za-z]?[\\\\dA-Za-z]{0,8}?)\\\\s*'",
            "'(?<vol>[A-Za-z]?[\\\\dA-Za-z]{0,8})\\\\s*'",
        ),
        (
            "rstrip removes a single dot only",
            "ref.title = rstripChars(body.join(' '), '.').trim();",
            "ref.title = body.join(' ').replace(/\\.$/, '').trim();",
        ),
        (
            "first year used instead of last",
            "ref.year = parseInt(years[years.length - 1], 10);",
            "ref.year = parseInt(years[0], 10);",
        ),
        (
            "|| instead of ?? when a year is already set",
            "ref.year = ref.year ?? parseInt(bm.groups.year, 10);",
            "ref.year = ref.year || parseInt(bm.groups.year, 10);",
        ),
        (
            "et al. not stripped before author parsing",
            "  s = s.replace(ET_AL, '');\n  if (CORPORATE_HINT.test(s)",
            "  if (CORPORATE_HINT.test(s)",
        ),
        (
            "abbreviated end page not expanded",
            "if (ldig.length < fdig.length) ldig = fdig.slice(0, fdig.length - ldig.length) + ldig;",
            "",
        ),
        (
            "corporate fallback when no surnames found removed",
            "if (!surnames.length && CORPORATE_HINT.test(s)) return [[], s];",
            "",
        ),
        (
            "bare-year tail fallback scans forwards",
            "for (let i = body.length - 1; i >= 0; i--) {",
            "for (let i = 0; i < body.length; i++) {",
        ),
    ]),
}

PROBES["scorer"] = ("scorer.js", [
    (
        "toFixed instead of round-half-even",
        "  const s = Math.abs(x).toFixed(100);",
        "  return (x < 0 ? '-' : '') + Math.abs(x).toFixed(2);",
    ),
    (
        "ecitmatch title guard lowered",
        "if (s.ecitmatch && s.title_sim >= ECITMATCH_TITLE_GUARD) return [true, 'ecitmatch', 0.99];",
        "if (s.ecitmatch) return [true, 'ecitmatch', 0.99];",
    ),
    (
        "fingerprint gate uses > instead of >=",
        "if (s.title_sim >= 0.90 && s.year_ok && s.locator_ok && s.type_ok) {",
        "if (s.title_sim > 0.90 && s.year_ok && s.locator_ok && s.type_ok) {",
    ),
    (
        "provider-agreement threshold 0.88 -> 0.89",
        "if (s.n_providers >= 2 && s.title_sim >= 0.88 && s.year_ok",
        "if (s.n_providers >= 2 && s.title_sim >= 0.89 && s.year_ok",
    ),
    (
        "confidence terms reordered (float associativity)",
        "  let score = 0.42 * s.title_sim;",
        "  let score = 0.0; score += 0.42 * s.title_sim;",
    ),
    (
        "author bonus uses overlap when author_ok",
        "  score += s.author_ok ? 0.10 : 0.06 * s.author_overlap;",
        "  score += s.author_ok ? 0.10 + 0.06 * s.author_overlap : 0.06 * s.author_overlap;",
    ),
    (
        "provider bonus not capped at 0.05",
        "  score += Math.min(0.05, 0.025 * (s.n_providers - 1));",
        "  score += 0.025 * (s.n_providers - 1);",
    ),
    (
        "type penalty dropped",
        "  if (!s.type_ok) score -= 0.15;",
        "",
    ),
    (
        "confidence not clamped",
        "  return Math.max(0.0, Math.min(1.0, score));",
        "  return score;",
    ),
    (
        "year_ok accepts a two-year gap",
        "    return this.year_delta !== null && this.year_delta <= 1;",
        "    return this.year_delta !== null && this.year_delta <= 2;",
    ),
    (
        "provider prefix not split for the count",
        "  s.n_providers = new Set([...providers].map((p) => p.split(':')[0])).size;",
        "  s.n_providers = new Set([...providers]).size;",
    ),
    (
        "consortium key replaces only the first tail word",
        "    txt = txt.split(tail).join(' '); // Python str.replace: every occurrence",
        "    txt = txt.replace(tail, ' ');",
    ),
    (
        "ranking tiebreak inverted",
        "    return -compareKeys(keyP[1], keyQ[1]);",
        "    return compareKeys(keyP[1], keyQ[1]);",
    ),
    (
        "ranking tiebreak dropped entirely",
        "    return -compareKeys(keyP[1], keyQ[1]);",
        "    return 0;",
    ),
    (
        "booleans not coerced in tuple comparison",
        "    const x = typeof a[i] === 'boolean' ? (a[i] ? 1 : 0) : a[i];",
        "    const x = a[i];",
    ),
    (
        "secondary-DOI preference inverted",
        "    !secondary,",
        "    secondary,",
    ),
    (
        "corporate similarity threshold 0.80 -> 0.70",
        "titleSimilarity(ref.corporate, cand.corporate || '') >= 0.80",
        "titleSimilarity(ref.corporate, cand.corporate || '') >= 0.70",
    ),
    (
        "book type check accepts journalArticle",
        "  if (ref.is_book) s.type_ok = ['book', 'bookSection'].includes(cand.item_type);",
        "",
    ),
])

HARNESS = {
    "fuzz": ["fuzz_parity.py", "--cases", "6000"],
    "utils": ["utils_parity.py"],
    "extractor": ["extractor_parity.py"],
    "scorer": ["scorer_parity.py"],
}


def run_harness(module: str) -> str:
    cmd = [sys.executable, str(HERE / HARNESS[module][0]), *HARNESS[module][1:]]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT
    )
    for line in (proc.stdout or "").splitlines():
        if line.startswith(("PASS", "FAIL")):
            return line.strip()
    tail = (proc.stderr or "").strip().splitlines()
    return f"ERROR {tail[-1] if tail else 'no output'}"


def check(module: str) -> tuple[int, int, list[str], list[str]]:
    """Returns (killed, expected_kills, unexpected_survivors, unexpected_kills)."""
    filename, probes = PROBES[module]
    path = SRC / filename
    original = path.read_text(encoding="utf-8")
    killed = expected = 0
    bad_survivors: list[str] = []
    bad_kills: list[str] = []
    print(f"\n{module} ({filename}) — {len(probes)} mutants")
    try:
        for label, find, replace in probes:
            equiv = label in EQUIVALENT
            if not equiv:
                expected += 1
            if find not in original:
                print(f"  {'STALE':<9} {label}  (pattern not found in source)")
                bad_survivors.append(f"{label} (stale pattern)")
                continue
            path.write_text(original.replace(find, replace, 1), encoding="utf-8")
            verdict = run_harness(module)
            was_killed = verdict.startswith("FAIL")
            if equiv:
                mark = "EQUIV-KILLED" if was_killed else "equivalent"
                if was_killed:
                    bad_kills.append(label)
            else:
                mark = "killed" if was_killed else "SURVIVED"
                if was_killed:
                    killed += 1
                else:
                    bad_survivors.append(label)
            print(f"  {mark:<12} {label:<50} {verdict}")
    finally:
        path.write_text(original, encoding="utf-8")
    return killed, expected, bad_survivors, bad_kills


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("modules", nargs="*", help=f"any of: {', '.join(PROBES)}")
    args = ap.parse_args()
    unknown = [m for m in args.modules if m not in PROBES]
    if unknown:
        ap.error(f"unknown module(s): {', '.join(unknown)}; choose from {', '.join(PROBES)}")
    modules = args.modules or list(PROBES)

    killed = total = 0
    survivors: list[str] = []
    surprises: list[str] = []
    for m in modules:
        k, t, s, ek = check(m)
        killed += k
        total += t
        survivors += s
        surprises += ek

    n_equiv = sum(1 for _, probes in PROBES.values() for lbl, _, _ in probes if lbl in EQUIVALENT)
    print(f"\nmutation score: {killed}/{total} killable mutants "
          f"({n_equiv} equivalent mutants excluded by design)")

    if survivors:
        print("\nSURVIVED — the harness cannot see these classes of bug:")
        for s in survivors:
            print(f"  - {s}")
    if surprises:
        print("\nAn 'equivalent' mutant was killed, so its equivalence argument is wrong:")
        for s in surprises:
            print(f"  - {s}\n      claimed: {EQUIVALENT[s]}")
    return 1 if (survivors or surprises) else 0


if __name__ == "__main__":
    sys.exit(main())

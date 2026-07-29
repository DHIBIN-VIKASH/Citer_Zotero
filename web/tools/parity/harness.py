"""Shared plumbing for the JS-vs-Python differential tests.

Every parity check works the same way: build a list of calls, run them through
the Python original and through the JS port, and require the results to be
identical. "Identical" is deliberately strict —

  * floats are compared as IEEE-754 bit patterns, not with a tolerance, because
    the scorer's gates are hard thresholds and a last-bit difference can flip a
    decision;
  * values are compared with their type tag, so a Python `False` can never
    silently match a JS `0` or `""` after a round trip through JSON.

Anything that cannot be represented faithfully in JSON is not compared here; it
is compared in the module-level harness that produced it.
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def bits(x: float) -> str:
    return struct.pack(">d", float(x)).hex()


def tag(v):
    """Type-tagged representation, mirroring the `tag()` in the .mjs runners."""
    if v is None:
        return {"t": "n"}
    if isinstance(v, bool):
        return {"t": "b", "v": v}
    if isinstance(v, (int, float)):
        return {"t": "f", "v": bits(v)}
    if isinstance(v, str):
        return {"t": "s", "v": v}
    if isinstance(v, (list, tuple)):
        return {"t": "a", "v": [tag(x) for x in v]}
    return {"t": "?", "v": str(v)}


def untag(d):
    """Human-readable form of a tagged value, for failure output."""
    t = d.get("t")
    if t == "n":
        return None
    if t == "f":
        return struct.unpack(">d", bytes.fromhex(d["v"]))[0]
    if t == "a":
        return [untag(x) for x in d["v"]]
    return d.get("v")


def run_node(runner: str | Path, payload: dict) -> list:
    proc = subprocess.run(
        ["node", str(Path(__file__).with_name(str(runner)))],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"node runner {runner} failed with exit {proc.returncode}")
    return json.loads(proc.stdout)


def report(name: str, calls: list, want: list, got: list, show: int = 12) -> int:
    """Compare tagged results and print a verdict. Returns a process exit code."""
    mismatches = [
        (call, w, g) for call, w, g in zip(calls, want, got) if w != g
    ]
    total = len(calls)
    if not mismatches:
        print(f"PASS  {name}: {total} calls, 0 mismatches")
        return 0

    print(f"FAIL  {name}: {len(mismatches)}/{total} mismatches")
    by_fn: dict[str, int] = {}
    for call, _, _ in mismatches:
        by_fn[call["fn"]] = by_fn.get(call["fn"], 0) + 1
    for fn, n in sorted(by_fn.items(), key=lambda kv: -kv[1]):
        print(f"    {fn:<22} {n}")
    for call, w, g in mismatches[:show]:
        print(f"\n  {call['fn']}({', '.join(ascii(a) for a in call['args'])})")
        print(f"    python = {untag(w)!r}")
        print(f"    js     = {untag(g)!r}")
    return 1

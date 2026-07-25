"""SQLite resolution cache and correction store.

Two tables with different lifetimes:

  resolutions — memoized API results, keyed by the normalized reference text.
                Safe to delete; only costs time to rebuild.
  corrections — decisions a human made in the review loop. These are the
                learning system: once you have told it what reference 17 means,
                that reference resolves silently forever, in this manuscript and
                in every future one.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .models import Candidate, ParsedRef, Resolution
from .utils import norm_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS resolutions (
    ref_key      TEXT PRIMARY KEY,
    normalized   TEXT,
    doi          TEXT,
    pmid         TEXT,
    status       TEXT,
    tier         TEXT,
    confidence   REAL,
    candidate    TEXT,   -- JSON blob of the winning Candidate
    resolved_at  REAL
);
CREATE TABLE IF NOT EXISTS corrections (
    ref_key      TEXT PRIMARY KEY,
    normalized   TEXT,
    raw_ref      TEXT,
    doi          TEXT,
    pmid         TEXT,
    candidate    TEXT,
    decided_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_res_doi ON resolutions(doi);
"""


def ref_key(ref: ParsedRef) -> str:
    """Stable key: normalized reference text, so whitespace/dash/case edits and
    a renumbered bibliography all still hit the same cache row."""
    return hashlib.sha1(norm_text(ref.raw).encode("utf-8")).hexdigest()


def _cand_to_json(c: Candidate | None) -> str | None:
    if not c:
        return None
    d = {k: v for k, v in c.__dict__.items() if k not in ("raw", "providers")}
    d["providers"] = sorted(c.providers or [])
    d["raw"] = c.raw if isinstance(c.raw, (dict, list)) else None
    return json.dumps(d, ensure_ascii=False)


def _cand_from_json(s: str | None) -> Candidate | None:
    if not s:
        return None
    d = json.loads(s)
    providers = set(d.pop("providers", []) or [])
    c = Candidate(**d)
    c.providers = providers or {c.provider}
    return c


class Cache:
    def __init__(self, path: str | Path = "database/cache.sqlite", enabled: bool = True):
        self.enabled = enabled
        self.path = Path(path)
        self.conn: sqlite3.Connection | None = None
        if not enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- corrections take priority over anything an API says ----------------
    def get_correction(self, ref: ParsedRef) -> Candidate | None:
        if not self.conn:
            return None
        row = self.conn.execute(
            "SELECT candidate FROM corrections WHERE ref_key=?", (ref_key(ref),)
        ).fetchone()
        return _cand_from_json(row[0]) if row else None

    def put_correction(self, ref: ParsedRef, cand: Candidate) -> None:
        if not self.conn:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO corrections VALUES (?,?,?,?,?,?,?)",
            (
                ref_key(ref),
                norm_text(ref.raw),
                ref.raw,
                cand.doi,
                cand.pmid,
                _cand_to_json(cand),
                time.time(),
            ),
        )
        self.conn.commit()

    # --- memoized resolutions ------------------------------------------------
    def get(self, ref: ParsedRef) -> Resolution | None:
        if not self.conn:
            return None
        row = self.conn.execute(
            "SELECT status, tier, confidence, candidate FROM resolutions WHERE ref_key=?",
            (ref_key(ref),),
        ).fetchone()
        if not row:
            return None
        status, tier, conf, cand_json = row
        return Resolution(
            n=ref.n,
            status=status,
            confidence=conf,
            tier=tier,
            candidate=_cand_from_json(cand_json),
            reason="from cache",
        )

    def put(self, ref: ParsedRef, res: Resolution) -> None:
        if not self.conn or res.status == "REVIEW":
            # never memoize a failure: the provider indexes improve over time
            return
        c = res.candidate
        self.conn.execute(
            "INSERT OR REPLACE INTO resolutions VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ref_key(ref),
                norm_text(ref.raw),
                c.doi if c else None,
                c.pmid if c else None,
                res.status,
                res.tier,
                res.confidence,
                _cand_to_json(c),
                time.time(),
            ),
        )
        self.conn.commit()

    def close(self) -> None:
        if self.conn:
            self.conn.close()

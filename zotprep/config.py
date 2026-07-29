"""Persisted user settings, so credentials are entered once and not per run.

Stored as JSON in the user's home directory, never in the repo — the file holds
a read/write Zotero API key. It is created 0600; on Windows that is advisory
only, which is why the file lives under the user profile rather than anywhere
shared.

Precedence, highest first:

  1. an explicit --zotero-userid / --zotero-key / --mailto on the command line
  2. $ZOTERO_USERID / $ZOTERO_KEY / $ZOTPREP_MAILTO
  3. this config file

so a saved credential never silently overrides one the caller asked for.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

FIELDS = ("zotero_userid", "zotero_key", "mailto")

ENV = {
    "zotero_userid": "ZOTERO_USERID",
    "zotero_key": "ZOTERO_KEY",
    "mailto": "ZOTPREP_MAILTO",
}


def config_path() -> Path:
    """$ZOTPREP_CONFIG wins, so tests and shared machines can redirect it."""
    override = os.environ.get("ZOTPREP_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".zotprep" / "config.json"


def load() -> dict:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in FIELDS and isinstance(v, str) and v}


def save(values: dict) -> Path:
    """Merge `values` into the stored config. Empty values are ignored, so a
    partial save never wipes a field the caller did not mention."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = load()
    merged.update({k: v for k, v in values.items() if k in FIELDS and v})
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # non-POSIX filesystem; the user-profile location is the real guard
    return path


def forget() -> bool:
    path = config_path()
    try:
        path.unlink()
        return True
    except OSError:
        return False


def resolve(cli: dict) -> dict:
    """Fill in whatever the command line did not supply, in precedence order."""
    stored = load()
    out = {}
    for field in FIELDS:
        out[field] = cli.get(field) or os.environ.get(ENV[field]) or stored.get(field) or None
    return out


def source_of(field: str, cli: dict) -> str:
    if cli.get(field):
        return "command line"
    if os.environ.get(ENV[field]):
        return f"${ENV[field]}"
    if load().get(field):
        return str(config_path())
    return "unset"

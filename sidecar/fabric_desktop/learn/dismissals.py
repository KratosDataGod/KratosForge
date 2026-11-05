"""Dismiss beside a best-practice finding (LX-09): findings this person chose to hide, kept on this PC
in ``<data>/learn/dismissed.json``.

A dismissal only hides a finding in this person's own Best practices panels. The check still runs,
and governance checks, reports and everything else that reads findings still see it. Three scopes:

    line   this rule on one line of one notebook or SQL tab, while the line reads the same
           (``line`` is the SHA-256 of the trimmed line, so no code is kept here)
    file   this rule anywhere in one notebook or SQL tab (``where``)
    never  this rule everywhere

Show again takes one away; Show all again takes them all away.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import jsonfile
from . import guides

SCOPES = ("line", "file", "never")
MAX = 1000          # the oldest go first past this
MAX_WHERE = 512
_HASH = re.compile(r"^[0-9a-f]{64}$")


def path(root: Path) -> Path:
    return Path(root) / "learn" / "dismissed.json"


def load(root: Path) -> list[dict[str, Any]]:
    """Every dismissal, oldest first. A file that isn't a list of them reads as none."""
    rows = jsonfile.read_json(path(root), [])
    return [r for r in rows if isinstance(r, dict) and r.get("scope") in SCOPES] if isinstance(rows, list) else []


def _key(rule_id: str, scope: str, where: str, line: str) -> str:
    return hashlib.sha256(f"{rule_id}|{scope}|{where}|{line}".encode()).hexdigest()[:16]


def add(root: Path, rule_id: str, scope: str, where: str = "", line: str = "") -> list[dict[str, Any]]:
    """Dismiss ``rule_id`` in ``scope``; dismissing the same thing again changes nothing."""
    rule_id, scope, where, line = str(rule_id), str(scope), str(where or ""), str(line or "")
    if rule_id not in guides.load():
        raise ValueError(f"{rule_id} isn't a notebook or SQL best-practice rule")
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
    if scope == "never":
        where = line = ""
    elif not where or len(where) > MAX_WHERE or not where.isprintable():
        raise ValueError(f"where must name the notebook or SQL tab, in at most {MAX_WHERE} characters")
    if scope == "file":
        line = ""
    elif scope == "line" and not _HASH.match(line):
        raise ValueError("line must be the SHA-256 of the line, as 64 hex characters")
    rows = load(root)
    key = _key(rule_id, scope, where, line)
    if not any(r.get("id") == key for r in rows):
        rows.append({"id": key, "rule_id": rule_id, "scope": scope, "where": where, "line": line,
                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        jsonfile.write_json(path(root), rows[-MAX:])
    return load(root)


def remove(root: Path, dismissal_id: str | None = None) -> list[dict[str, Any]]:
    """Show one dismissed finding again, or all of them when ``dismissal_id`` is None."""
    rows = load(root)
    kept = [] if dismissal_id is None else [r for r in rows if r.get("id") != str(dismissal_id)]
    if len(kept) != len(rows):
        jsonfile.write_json(path(root), kept)
    return kept

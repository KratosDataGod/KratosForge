"""The hash chain both audit trails use: the app's (audit.py) and external AI tools' (integrations/audit.py).

Lines go to ``<folder>/audit-YYYY-MM.jsonl``. Each line's ``hash`` is SHA-256 over the line before
it (``prev``) and its own content, so ``verify()`` finds a line that was deleted or changed.

Several processes append to the same folder (the app, scheduled runs, the agent, one MCP server per
connected tool). ``append()`` takes a lock file in the folder and reads the last line's hash under
it, so two writers can't both chain onto the same line and fork it (HD-017, QA-012).

Deleting lines from the end leaves a chain that still checks out, so each write also records the
newest hash and the line count in ``<folder>/head.json``, and ``verify()`` reports a trail that
ends before it. If the newest line is gone when the next write comes, the old head is kept in
``head.json`` as ``lost``, so later lines don't hide it. Anyone who can rewrite the folder can
rewrite this file too (and the whole chain): it catches lines cut off by a tool or by hand, not
an attacker with write access.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import jsonfile

GENESIS = "0" * 64
HEAD = "head.json"  # the newest hash and line count, checked by verify()
_TAIL = 64 * 1024  # bytes read from the end of a file at a time, looking for its last line


def hash_entry(prev: str, entry: dict[str, Any]) -> str:
    body = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev + body).encode()).hexdigest()


def files(folder: Path) -> list[Path]:
    return sorted(Path(folder).glob("audit-*.jsonl"))


def _last_line(path: Path) -> str:
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        size = _TAIL
        while True:
            start = max(0, end - size)
            f.seek(start)
            lines = [line for line in f.read(end - start).splitlines() if line.strip()]
            if lines and (start == 0 or len(lines) > 1):  # the first line read may be cut off; the last isn't
                return lines[-1].decode("utf-8", "replace")
            if start == 0:
                return ""
            size *= 2


def last_hash(folder: Path) -> str:
    """The newest line's hash, or GENESIS for an empty folder. A damaged last line also gives GENESIS:
    ``verify()`` reports it, and recording goes on."""
    for path in reversed(files(folder)):
        line = _last_line(path)
        if line:
            try:
                return str(json.loads(line)["hash"])
            except (ValueError, KeyError, TypeError):
                return GENESIS
    return GENESIS


@contextmanager
def locked(folder: Path) -> Iterator[None]:
    """One writer at a time across every process, through ``<folder>/.lock``."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a+b") as f:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # retries for about a second, then raises
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def seal(prev: str, entry: dict[str, Any]) -> str:
    """Chains ``entry`` onto ``prev`` (sets its prev and hash) and returns the line to write."""
    entry["prev"] = prev
    entry["hash"] = hash_entry(prev, entry)
    return json.dumps(entry, default=str)


def _hashes(folder: Path) -> list[str | None]:
    """Each line's stored hash in trail order (None for a line that isn't an audit line)."""
    out: list[str | None] = []
    for path in files(folder):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(str(json.loads(line)["hash"]))
                except (ValueError, KeyError, TypeError):
                    out.append(None)
    return out


def _head(folder: Path) -> dict[str, Any]:
    head = jsonfile.read_json(Path(folder) / HEAD, {})
    return head if isinstance(head, dict) else {}


def write(folder: Path, entry: dict[str, Any], line: str) -> None:
    """Appends a sealed line to its month's file and moves the head on. Call it holding the chain's lock."""
    folder = Path(folder)
    head = _head(folder)
    with (folder / f"audit-{str(entry['at'])[:7]}.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    new = {"hash": entry["hash"]}
    if head.get("hash") == entry["prev"] and isinstance(head.get("lines"), int):
        new["lines"] = head["lines"] + 1
    else:  # no head yet (a trail from before HD-017), a write that stopped before its head, or lines gone
        hashes = _hashes(folder)
        new["lines"] = len(hashes)
        lines = head.get("lines")
        if head.get("hash") and not (isinstance(lines, int) and 0 < lines <= len(hashes) and hashes[lines - 1] == head["hash"]):
            new["lost"] = {"hash": head["hash"], "lines": lines}
    if head.get("lost"):  # the first loss stays on record
        new["lost"] = head["lost"]
    jsonfile.write_json(folder / HEAD, new)


def append(folder: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Chains ``entry`` onto the folder's newest line and appends it, under the lock."""
    with locked(folder):
        write(folder, entry, seal(last_hash(folder), entry))
    return entry


def verify(folder: Path) -> dict[str, Any]:
    """Walks the chain: every line's hash must match its content and the line before it."""
    prev, count, problems = GENESIS, 0, []
    seen: dict[int, str] = {}  # line number across the trail -> its hash
    for path in files(folder):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            count += 1
            try:
                entry = json.loads(line)
                stored = entry.pop("hash")
            except (ValueError, KeyError, AttributeError):
                problems.append(f"{path.name} line {number}: not an audit line")
                continue
            if entry.get("prev") != prev:
                problems.append(f"{path.name} line {number}: a line before it is missing or changed")
            if hash_entry(entry.get("prev", ""), entry) != stored:
                problems.append(f"{path.name} line {number}: this line was changed")
            prev = stored
            seen[count] = stored
    head = _head(folder)
    for name, mark in (("newest line recorded", head), ("line recorded before lines went missing", head.get("lost") or {})):
        lines, want = mark.get("lines"), mark.get("hash")
        if want and isinstance(lines, int) and seen.get(lines) != want:
            problems.append(f"{HEAD}: the trail ends before the {name} (line {lines}); lines were deleted from the end")
    return {"lines": count, "ok": not problems, "problems": problems}

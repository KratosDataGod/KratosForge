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
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]  # Windows-only; retries for about a second, then raises
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]  # Windows-only
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
            except (ValueError, KeyError, AttributeError, TypeError):  # TypeError: a JSON array
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


# ---------------------------------------------------------------- exports IT can check on their own

EXPORT_SCHEMA = "audit-export@1"


def manifest_path(out: Path) -> Path:
    return Path(str(out) + ".manifest.json")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def export(folder: Path, out: Path, since: str = "") -> dict[str, Any]:
    """Copy the trail, from the first line at or after ``since`` (an ISO date or time; all of it when
    empty) to the newest, into ``out``, and write ``<out>.manifest.json`` beside it. The lines are
    copied as they are and without gaps, so the copy is a chain of its own: its first line's ``prev``
    and last line's ``hash`` are in the manifest with the line count and the file's SHA-256, and
    ``verify_export()`` checks all of them with nothing but the two files."""
    folder, out = Path(folder), Path(out)
    inside = Path(folder).resolve()
    for path in (out, manifest_path(out)):
        if path.resolve() == inside or inside in path.resolve().parents:
            # A copy in the trail's own folder would replace a month's file or be read as part of the trail.
            raise ValueError(f"Save the export outside the audit folder ({folder}): a file there is part of the trail")
    lines: list[str] = []
    with locked(folder):  # a still copy: no line lands half-way through
        for path in files(folder):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                if not lines and since:
                    try:
                        at = str(json.loads(line).get("at", ""))
                    except (ValueError, AttributeError):
                        at = ""
                    if at < since:
                        continue
                lines.append(line)
        head = _head(folder)
    out.parent.mkdir(parents=True, exist_ok=True)
    jsonfile.write_text(out, "".join(line + "\n" for line in lines), newline="\n")
    first = json.loads(lines[0]) if lines else {}
    last = json.loads(lines[-1]) if lines else {}
    manifest = {
        "schema": EXPORT_SCHEMA, "lines": len(lines), "since": since, "first_prev": first.get("prev", GENESIS),
        "first_at": first.get("at", ""), "last_hash": last.get("hash", GENESIS), "last_at": last.get("at", ""),
        "sha256": _sha256(out), "trail_head": {"hash": head.get("hash", ""), "lines": head.get("lines")},
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    jsonfile.write_json(manifest_path(out), manifest)
    return {"file": str(out), "manifest": str(manifest_path(out)), **manifest}


def verify_export(out: Path, folder: Path | None = None) -> dict[str, Any]:
    """Check an export: the file is the one the manifest describes (SHA-256, line count), every line's
    hash matches its content and the line before it, and the chain runs from the manifest's first
    ``prev`` to its last ``hash``. Anyone can rebuild a file and its manifest so they agree, so with
    ``folder`` (the trail it came from) the export's lines must also be the trail's own lines."""
    out = Path(out)
    problems: list[str] = []
    manifest = jsonfile.read_json(manifest_path(out), None)
    if not isinstance(manifest, dict) or manifest.get("schema") != EXPORT_SCHEMA:
        return {"ok": False, "lines": 0, "problems": [f"{manifest_path(out).name}: missing or not an audit export manifest"]}
    if not out.exists():
        return {"ok": False, "lines": 0, "problems": [f"{out.name}: missing"]}
    if _sha256(out) != manifest.get("sha256"):
        problems.append(f"{out.name}: its SHA-256 isn't the manifest's: the file was changed after it was exported")
    prev, count, hashes = str(manifest.get("first_prev", GENESIS)), 0, []
    for number, line in enumerate(out.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        count += 1
        try:
            entry = json.loads(line)
            stored = entry.pop("hash")
        except (ValueError, KeyError, AttributeError, TypeError):
            problems.append(f"line {number}: not an audit line")
            continue
        if entry.get("prev") != prev:
            problems.append(f"line {number}: a line before it is missing or changed")
        if hash_entry(entry.get("prev", ""), entry) != stored:
            problems.append(f"line {number}: this line was changed")
        prev = str(stored)
        hashes.append(prev)
    if count != manifest.get("lines"):
        problems.append(f"{count} lines, but the manifest says {manifest.get('lines')}")
    if count and prev != manifest.get("last_hash"):
        problems.append("the last line isn't the one the manifest names: lines were cut from the end")
    if folder is not None and hashes:
        trail = [h for h in _hashes(Path(folder)) if h]
        if hashes[0] not in trail:
            problems.append("the export's lines aren't in this trail")
        else:
            start = trail.index(hashes[0])
            if trail[start:start + len(hashes)] != hashes:
                problems.append("the export's lines differ from this trail's")
    return {"ok": not problems, "lines": count, "problems": problems}

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

So the head is also written, each time it moves, to the Windows Application event log, which a
user who isn't an administrator can add to but not change or clear (audit_anchor.py). ``verify()``
compares the trail with the newest record there for this folder: a trail that ends before it, or
whose line at its line number has another hash (the chain re-sealed), is reported, and so is a
missing ``head.json`` when such a record exists (only a version that writes ``head.json`` writes
them; nothing in the lines themselves tells a trail from before ``head.json`` apart). With no record
to read (off Windows, or an event log that can't be read), ``verify()`` adds a note on Windows and
relies on ``head.json`` alone.

An organization can cap how long a trail is kept (policy ``audit_retention_days``, retention.py).
``prune()`` then deletes whole month files whose month ended longer ago, oldest first, never the
newest file. Before it deletes anything it records in ``head.json`` where the trail now starts
(``base``: the last deleted line's hash, how many lines and through which month), so ``verify()``
accepts the first remaining line chaining onto it and the line counts still add up. The caller
writes an audit line saying what goes before calling it. ``base`` has the same limit as the rest
of ``head.json``: someone who can rewrite the folder can delete old months and write a ``base``
that matches, so it shows what a prune removed, not that no one else removed anything.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import audit_anchor, jsonfile

GENESIS = "0" * 64
HEAD = "head.json"  # the newest hash and line count, checked by verify()
CLOCK_NOTE_SECONDS = 5 * 60  # a line this much earlier than the one before it is noted: the clock was set back
MAX_NOTES = 20
_TAIL = 64 * 1024  # bytes read from the end of a file at a time, looking for its last line
_MONTH = re.compile(r"\d{4}-\d{2}")  # audit-YYYY-MM.jsonl


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


def _ends_mid_line(path: Path) -> bool:
    """Whether ``path`` ends part way through a line: an append a power cut stopped before its newline."""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return False
            f.seek(-1, os.SEEK_END)
            return f.read(1) != b"\n"
    except OSError:
        return False


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
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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
    """Appends a sealed line to its month's file and moves the head on. Call it holding the chain's lock.
    A line whose time is in an earlier month than the newest file (this PC's clock was set back across
    the start of a month) goes in the newest file, so the files stay in trail order and ``verify()``
    doesn't take the line for one moved; its ``at`` still says what the clock said."""
    folder = Path(folder)
    head = _head(folder)
    name = f"audit-{str(entry['at'])[:7]}.jsonl"
    newest = files(folder)
    if newest and newest[-1].name > name:
        name = newest[-1].name
    # A line a power cut left without its newline stays as it is (the trail is never rewritten; verify()
    # reports it), and this one starts on a line of its own instead of being joined onto it.
    cut = _ends_mid_line(folder / name)
    with (folder / name).open("a", encoding="utf-8") as f:
        f.write(("\n" if cut else "") + line + "\n")
    new = {"hash": entry["hash"]}
    if not cut and head.get("hash") == entry["prev"] and isinstance(head.get("lines"), int):
        new["lines"] = head["lines"] + 1
    else:  # no head yet (a trail from before HD-017), a write that stopped before its head, or lines gone
        hashes = _hashes(folder)
        new["lines"] = len(hashes)
        lines = head.get("lines")
        if head.get("hash") and not (isinstance(lines, int) and 0 < lines <= len(hashes) and hashes[lines - 1] == head["hash"]):
            new["lost"] = {"hash": head["hash"], "lines": lines}
    if head.get("lost"):  # the first loss stays on record
        new["lost"] = head["lost"]
    if head.get("base"):  # where a pruned trail starts
        new["base"] = head["base"]
    jsonfile.write_json(folder / HEAD, new)
    _anchor(folder, new)


def _anchor(folder: Path, head: dict[str, Any]) -> None:
    """The head, also in the Windows event log (audit_anchor.py), counting lines a prune deleted."""
    if isinstance(head.get("lines"), int):
        audit_anchor.record(folder, str(head.get("hash") or ""), head["lines"] + _base_lines(head))


def _base_lines(head: dict[str, Any]) -> int:
    base = head.get("base")
    lines = base.get("lines") if isinstance(base, dict) else 0
    return lines if isinstance(lines, int) else 0


def append(folder: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Chains ``entry`` onto the folder's newest line and appends it, under the lock."""
    with locked(folder):
        write(folder, entry, seal(last_hash(folder), entry))
    return entry


def _month(path: Path) -> str:
    return path.stem[len("audit-"):]


def _month_end(month: str) -> float:
    """The moment the month ``YYYY-MM`` ended (UTC), as a timestamp."""
    import datetime as dt
    year, mon = (int(x) for x in month.split("-"))
    start = dt.datetime(year + mon // 12, mon % 12 + 1, 1, tzinfo=dt.timezone.utc)
    return start.timestamp()


def prunable(folder: Path, days: int, now: float | None = None) -> list[Path]:
    """The month files ``prune`` would delete: oldest first, every month that ended more than ``days``
    days ago by the clock (one that ended exactly ``days`` days ago stays), never the newest file. None
    when ``days`` is 0 or less (the trail is kept). Only the clock and the limit decide: ``head.json`` is
    never read here, so nothing written in it changes which months are removed. The newest file is the
    newest one up to the clock's own month, so a file named after a later month doesn't take its place."""
    if days <= 0:
        return []
    t = now if now is not None else time.time()
    cutoff = t - days * 86400
    every = [p for p in files(folder) if _MONTH.fullmatch(_month(p))]
    this_month = dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m")
    current = [p for p in every if _month(p) <= this_month]
    newest = current[-1] if current else (every[-1] if every else None)
    return [p for p in every if p != newest and _month_end(_month(p)) < cutoff]


def prune(folder: Path, days: int, now: float | None = None) -> dict[str, Any]:
    """Delete the month files ``prunable`` names, under the chain's lock: ``head.json`` first records
    where the trail now starts, then the files go, oldest first. A prune stopped part way is finished
    by the next one (the head already says where the trail starts). Returns what went."""
    folder = Path(folder)
    with locked(folder):
        old = prunable(folder, days, now)
        if not old:
            return {"files": [], "lines": 0}
        head = _head(folder)
        base: dict[str, Any] = head["base"] if isinstance(head.get("base"), dict) else {}
        done = str(base.get("through") or "")  # a prune stopped part way already counted these months
        lines = sum(1 for p in old if _month(p) > done
                    for line in p.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
        last = _last_line(old[-1])
        try:
            last_hash = str(json.loads(last)["hash"])
        except (ValueError, KeyError, TypeError):  # a damaged last line: verify() reports the break that follows
            last_hash = ""
        new = dict(head)
        # The head only moves forward: after a prune stopped part way and a clock put back, months it already
        # counted may wait for the clock; ``through`` stays where it was, and they aren't counted again.
        new["base"] = {"hash": last_hash, "through": max(done, _month(old[-1])),
                       "lines": int(base.get("lines") or 0) + lines}
        if isinstance(head.get("lines"), int):
            new["lines"] = max(0, head["lines"] - lines)
        lost = head.get("lost")
        if isinstance(lost, dict) and isinstance(lost.get("lines"), int):
            if lost["lines"] > lines:
                new["lost"] = {**lost, "lines": lost["lines"] - lines}
            else:  # lines lost before the cut: they were in the history deleted now
                new.pop("lost", None)
        jsonfile.write_json(folder / HEAD, new)
        _anchor(folder, new)
        for path in old:
            path.unlink(missing_ok=True)
    return {"files": [p.name for p in old], "lines": lines, "through": _month(old[-1])}



def verify(folder: Path) -> dict[str, Any]:
    """Walks the chain: every line's hash must match its content and the line before it. After a
    prune, the first line may chain onto the last deleted line (``head.json`` ``base``)."""
    prev, count, problems = GENESIS, 0, []
    notes: list[str] = []
    head = _head(folder)
    base: dict[str, Any] = head["base"] if isinstance(head.get("base"), dict) else {}
    seen: dict[int, str] = {}  # line number across the trail -> its hash
    last_at: dt.datetime | None = None
    for path in files(folder):
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            count += 1
            try:
                entry = json.loads(line)
                stored = entry.pop("hash")
            except (ValueError, KeyError, AttributeError, TypeError):  # TypeError: a JSON array
                problems.append(f"{path.name} line {number}: not an audit line")
                continue
            if count == 1 and base.get("hash") and entry.get("prev") == base["hash"]:
                prev = base["hash"]  # the trail starts after a prune
            if entry.get("prev") != prev:
                problems.append(f"{path.name} line {number}: a line before it is missing or changed")
            if hash_entry(entry.get("prev", ""), entry) != stored:
                problems.append(f"{path.name} line {number}: this line was changed")
            prev = stored
            seen[count] = stored
            at = _moment(str(entry.get("at", "")))
            if at is not None and last_at is not None and (last_at - at).total_seconds() > CLOCK_NOTE_SECONDS \
                    and len(notes) < MAX_NOTES:
                notes.append(f"{path.name} line {number}: its time is {_span(last_at - at)} earlier than the line "
                             "before it, so this PC's clock was changed (the chain itself is intact)")
            last_at = at if at is not None and (last_at is None or at > last_at) else last_at
    # Lines in months a prune already counted, still waiting to be deleted (the clock was put back after a
    # prune stopped part way), aren't in the head's count of lines.
    counted = sum(1 for path in files(folder) if base.get("through") and _month(path) <= str(base["through"])
                  for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
    seen = {n - counted: h for n, h in seen.items()}
    for name, mark in (("newest line recorded", head), ("line recorded before lines went missing", head.get("lost") or {})):
        lines, want = mark.get("lines"), mark.get("hash")
        if want and isinstance(lines, int) and seen.get(lines) != want:
            problems.append(f"{HEAD}: the trail ends before the {name} (line {lines}); lines were deleted from the end")
    try:  # the head file as it is now: an unchanged head reuses the last read of the event log
        stat = (Path(folder) / HEAD).stat()
        key: object = (stat.st_size, stat.st_mtime_ns, count)
    except OSError:
        key = ("no head", count)
    anchor, note = audit_anchor.newest(folder, key)
    if note:
        notes.append(note)
    if anchor is not None:
        problems.extend(_against(anchor, head, seen, count, missing=not (Path(folder) / HEAD).exists()))
    if base.get("through") and not base.get("hash") and len(notes) < MAX_NOTES:
        # A prune whose last deleted line was damaged leaves no hash to chain onto: the kept trail then starts
        # from the beginning again and checks out on its own, so say what can no longer be checked.
        notes.append("The last pruned line was damaged, so where the kept trail starts can't be checked")
    result: dict[str, Any] = {"lines": count, "ok": not problems, "problems": problems}
    if notes:  # only when there's something to say: the usual result keeps its shape
        result["notes"] = notes
    if base.get("through"):
        result["pruned"] = {"through": base["through"], "lines": base.get("lines")}
    return result


def _against(anchor: audit_anchor.Anchor, head: dict[str, Any], seen: dict[int, str], count: int,
             missing: bool) -> list[str]:
    """The trail against the newest end the event log recorded. Only a version that writes head.json
    writes these records, so with one on record a missing head.json was deleted."""
    problems = [f"{HEAD}: missing, so the end of the trail can't be confirmed"] if missing else []
    pruned = _base_lines(head)
    line = anchor.lines - pruned  # its line number in the trail as it now starts
    if line > count:
        problems.append(f"event log: the trail ends before the newest line the Windows event log recorded "
                        f"(line {line}), so lines were deleted from the end")
    elif line > 0 and seen.get(line) != anchor.hash:
        problems.append(f"event log: line {line} isn't the line the Windows event log recorded, so the trail "
                        f"was re-sealed at or before line {line}")
    elif line == 0 and pruned and anchor.hash != (head.get("base") or {}).get("hash"):
        problems.append("event log: the trail's start isn't the one the Windows event log recorded")
    return problems


def _span(delta: dt.timedelta) -> str:
    minutes = round(delta.total_seconds() / 60)
    if minutes < 120:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = round(minutes / 60)
    return f"{hours} hours" if hours < 48 else f"{round(hours / 24)} days"


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


def _moment(text: str) -> dt.datetime | None:
    """An ISO date or time as an aware UTC time (a date, or a time with no offset, is UTC); None if it
    isn't one. Compared as times, not text, so "20261101" and "+02:00" cut where they say."""
    try:
        value = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)


def export(folder: Path, out: Path, since: str = "", replace: bool = False) -> dict[str, Any]:
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
    if not replace and (out.exists() or manifest_path(out).exists()):
        raise ValueError(f"{out} is there already: choose another name, or replace it on purpose")
    start = _moment(since) if since else None
    if since and start is None:
        raise ValueError(f"--since {since!r} isn't a date: use YYYY-MM-DD, or a time such as "
                         "2026-11-01T09:00:00+00:00")
    lines: list[str] = []
    with locked(folder):  # a still copy: no line lands half-way through
        for path in files(folder):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                if not lines and start is not None:
                    try:
                        at = _moment(str(json.loads(line).get("at", "")))
                    except (ValueError, AttributeError):
                        at = None
                    if at is None or at < start:
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
    for number, line in enumerate(out.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
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

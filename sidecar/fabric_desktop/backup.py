"""Backing up and restoring what this PC keeps for KratosForge (FD-126).

A backup is a zip of the app's folder (``%LOCALAPPDATA%\\FabricDesktop``): settings, notebooks,
schedules on this PC, run history, receipts and the local databases. With ``agent_home`` it also
holds a headless agent's queue and alert databases. It never holds:

- sign-in tokens (``msal_cache.bin``, the embedded Fabric pages' saved sign-in) and the running
  app's link token (``link-port``), which a restore would write over the live one,
- the engine, caches, logs and lock files, which the app rebuilds,
- a Teams webhook address, which lets anyone post to that channel (set it again after a restore),
- anything credential-shaped inside a text file (``redact.secrets``); the manifest names each
  file that had something removed.

Every file's SHA-256 is in ``manifest.json``. ``restore`` checks the whole zip before it writes
anything, previews by default, refuses while something has a database open, and keeps a copy of
each file it replaces, so a restore can be undone by hand. It adds and replaces files and checks
each one it writes against the manifest. The only files it takes away are a replaced database's
``-wal``, ``-shm`` and ``-journal`` files, which would otherwise be replayed onto the restored
database; they go into the safety folder beside the database's copy.
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
import zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .redact import secrets
from . import jsonfile
from . import trees
from .tmdl import _RESERVED as _DEVICE_NAMES  # Windows device names (NUL, CON, COM1 and the rest)

_log = logging.getLogger(__name__)

SCHEMA = "fabric-desktop-backup@1"
MANIFEST = "manifest.json"
SAFETY = "restore-safety"  # where restore keeps the files it replaced
# Restores whose replaced files are kept in SAFETY: the newest ones. Each holds full copies of the
# databases and settings it replaced, so years of restores can't keep every old copy on the PC.
KEEP_SAFETY = 5
_STAMP = re.compile(r"\d{8}-\d{6}")
# Top level only. fabric-view: the Fabric view's browser profile, whose cookies are its sign-in.
SKIP_DIRS = {"engine", "onelake-cache", "EBWebView", "fabric-view", "logs", "schedule_locks", "_leases", SAFETY}
# link-port and editor-port: this run's link listener token and VS Code pairing hello.
SKIP_FILES = {"msal_cache.bin", "fabric-view-signin.bin", "link-port", "editor-port"}
SKIP_SUFFIXES = {".log", ".tmp", ".lock", ".pyc"}
# keys/: this PC's own keys (keyed_fingerprint's). A restore makes new ones, so fingerprints change after it.
KEY_DIRS = {"keys"}
KEYS_REASON = "tokens and this PC's keys are never backed up"
SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
SQLITE_HEADER = b"SQLite format 3\x00"
# A manifest lists each file's path, size and SHA-256: a few hundred bytes a file. Its size is checked
# before it is read, since a zip's entries can be far larger once unpacked.
MAX_MANIFEST = 64 * 1024 * 1024
# What a damaged or foreign zip raises while a member is read: a bad CRC or header, broken deflate data,
# a compression method or zip version Python can't read, an encrypted member.
_UNREADABLE = (OSError, EOFError, zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError)
SQLITE_COMPANIONS = ("-wal", "-shm", "-journal")  # SQLite replays these onto the database they sit beside
TEXT_SUFFIXES = {".json", ".jsonl", ".toml", ".ipynb", ".sql", ".py", ".md", ".txt", ".csv", ".yaml", ".yml"}
SECRET_KEYS = {"teams_webhook", "platform_notify"}  # platform_notify holds webhook addresses
AGENT_FILES = ("queue.sqlite3", "alerts.sqlite3")
DB_WAIT = 10.0  # seconds a backup waits for a database another program is writing to
# The audit trails, under app/ (this PC's data folder) and agent/ (the agent's home): this PC's, and the
# integrations'. A backup keeps them, and a restore never writes them.
AUDIT_TRAILS = (("audit",), ("integrations", "audit"))
AUDIT_REASON = "restore leaves the audit trails as they are"


class BackupError(ValueError):
    pass


def _skipped(rel: PurePosixPath) -> str | None:
    if len(rel.parts) > 1 and rel.parts[0] in KEY_DIRS:
        return KEYS_REASON
    if (len(rel.parts) > 1 and rel.parts[0] in SKIP_DIRS) or "__pycache__" in rel.parts:
        return "rebuilt by the app"
    if rel.name in SKIP_FILES:
        return "tokens are never backed up"
    if rel.suffix.lower() in SKIP_SUFFIXES or rel.name.endswith(SQLITE_COMPANIONS):
        return "rebuilt by the app"
    return None


def _audit_trail(name: str) -> str | None:
    """The audit trail folder (``app/audit/``) a backup's ``name`` is in or names, or None. Matched by name
    as Windows reads a path: any case, and a trailing dot or space dropped from each part."""
    parts = [p.rstrip(" .").lower() for p in PurePosixPath(name).parts]
    if len(parts) < 2 or parts[0] not in ("app", "agent"):
        return None
    for trail in AUDIT_TRAILS:
        if tuple(parts[1:1 + len(trail)]) == trail:
            return "/".join((parts[0], *trail)) + "/"
    return None


_FOLDED_DIRS = {d.casefold() for d in SKIP_DIRS}
_FOLDED_FILES = {f.casefold() for f in SKIP_FILES}


def _skipped_any_case(rel: PurePosixPath) -> str | None:
    """:func:`_skipped` with names compared as Windows compares them, ignoring case."""
    parts = tuple(part.casefold() for part in rel.parts)
    if len(parts) > 1 and parts[0] in KEY_DIRS:
        return KEYS_REASON
    if (len(parts) > 1 and parts[0] in _FOLDED_DIRS) or "__pycache__" in parts:
        return "rebuilt by the app"
    if parts[-1] in _FOLDED_FILES:
        return "tokens are never backed up"
    return _skipped(PurePosixPath(*parts))


def _sqlite_bytes(path: Path) -> bytes:
    """A consistent copy of a database that may be open, through SQLite's own backup. The read lock
    is taken first, waiting at most ``DB_WAIT``: Python's ``backup`` would otherwise wait for ever
    on a database another program holds a write lock on."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "copy.sqlite"
        src = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=DB_WAIT, isolation_level=None)
        dst = sqlite3.connect(copy)
        try:
            src.execute("BEGIN")
            src.execute("SELECT count(*) FROM sqlite_master").fetchall()  # the read lock, held through the copy
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        return copy.read_bytes()


def _database_copy(name: str, path: Path) -> bytes:
    """The database's consistent copy. A file SQLite can't read as a database goes in as it is
    (#1674); one that stays locked, or can't be read for another reason, stops the backup rather
    than going in half written."""
    try:
        return _sqlite_bytes(path)
    except sqlite3.Error as exc:
        if getattr(exc, "sqlite_errorcode", None) in _DAMAGED:
            return path.read_bytes()
        if getattr(exc, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise BackupError(f"No backup was saved: {name} was being written to for more than {DB_WAIT:g} "
                              "seconds. Try again in a moment, or close KratosForge and stop the agent first.") from None
        raise BackupError(f"No backup was saved: {name} couldn't be read ({exc}).") from None


def _whole_lines(data: bytes) -> bytes:
    """A ``.jsonl`` file without a last line still being appended when it was read."""
    cut = data.rfind(b"\n") + 1
    return data[:cut] if cut else data


def _clean_text(rel: PurePosixPath, data: bytes) -> tuple[bytes, list[str]]:
    """Text without webhook addresses or anything credential-shaped, and what was taken out."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data, []
    removed: list[str] = []
    if rel.suffix == ".json":
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and SECRET_KEYS & obj.keys():
            removed += sorted(SECRET_KEYS & obj.keys())
            for key in SECRET_KEYS:
                obj.pop(key, None)
            text = json.dumps(obj, indent=2)
    cleaned = secrets(text)
    if cleaned != text:
        removed.append("something that looks like a credential")
    return cleaned.encode("utf-8"), removed


def _forget_engine(data: bytes) -> bytes:
    """Schedules on this PC without the engine they were registered with, so the app registers
    them with Windows Task Scheduler again the first time it starts after a restore."""
    try:
        obj = json.loads(data)
    except ValueError:
        return data
    if isinstance(obj, dict):
        obj.pop("engine", None)
    return json.dumps(obj, indent=1).encode("utf-8")


def _collect(data_dir: Path, agent_home: Path | None) -> tuple[list[tuple[str, Path]], list[dict[str, str]]]:
    files: list[tuple[str, Path]] = []
    skipped: list[dict[str, str]] = []
    if data_dir.is_dir():
        for path in sorted(p for p in data_dir.rglob("*") if p.is_file()):
            rel = PurePosixPath(path.relative_to(data_dir).as_posix())
            reason = _skipped(rel)
            if reason:
                if reason.startswith("tokens"):
                    skipped.append({"path": f"app/{rel}", "reason": reason})
                continue
            if not _safe(f"app/{rel}"):  # restore would refuse it, so the backup leaves it out and says so
                skipped.append({"path": f"app/{rel}", "reason": "its name looks like a Windows short name (such as draft~1.txt), so rename the file to back it up"})
                continue
            files.append((f"app/{rel}", path))
    if agent_home:
        for name in AGENT_FILES:
            if (agent_home / name).is_file():
                files.append((f"agent/{name}", agent_home / name))
    return files, skipped


def create(out_dir: Path, data_dir: Path, agent_home: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Writes a backup zip into ``out_dir`` and returns its path and manifest."""
    now = now or datetime.now(timezone.utc)
    files, skipped = _collect(data_dir, agent_home)
    entries: list[dict[str, Any]] = []
    cleaned: list[dict[str, str]] = []
    target = out_dir / f"fabric-desktop-backup-{now.strftime('%Y%m%d-%H%M%S')}.zip"
    with jsonfile.long_path_named(target, "written"):  # a folder past Windows' 260 without long paths: named
        out_dir.mkdir(parents=True, exist_ok=True)
    # Written as .part and renamed once whole, so a backup that stops part way leaves no zip behind.
    part = target.with_name(target.name + ".part")
    try:
        _write(part, files, skipped, entries, cleaned, now)
        jsonfile.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return {"path": str(target), "files": len(entries), "cleaned": cleaned}


def _write(part: Path, files: list[tuple[str, Path]], skipped: list[dict[str, str]], entries: list[dict[str, Any]],
           cleaned: list[dict[str, str]], now: datetime) -> None:
    with jsonfile.long_path_named(part.with_name(part.name.removesuffix(".part")), "written"), open(part, "wb") as f:
        with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
            _fill(z, files, skipped, entries, cleaned, now)
        f.flush()
        os.fsync(f.fileno())


def _fill(z: zipfile.ZipFile, files: list[tuple[str, Path]], skipped: list[dict[str, str]],
          entries: list[dict[str, Any]], cleaned: list[dict[str, str]], now: datetime) -> None:
    for name, path in files:
        rel = PurePosixPath(name)
        if rel.suffix.lower() in SQLITE_SUFFIXES or _has_sqlite_header(path):
            data = _database_copy(name, path)
        else:
            data = path.read_bytes()
        if rel.suffix.lower() == ".jsonl":
            data = _whole_lines(data)
        if name == "app/schedules.json":
            data = _forget_engine(data)
        if rel.suffix.lower() in TEXT_SUFFIXES:
            data, removed = _clean_text(rel, data)
            if removed:
                cleaned.append({"path": name, "removed": ", ".join(removed)})
        z.writestr(name, data)
        entries.append({"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    trails = sorted({t for e in entries if (t := _audit_trail(e["path"]))})
    manifest = {"schema": SCHEMA, "created": now.isoformat(timespec="seconds"), "files": entries,
                "cleaned": cleaned, "not_included": skipped,
                "not_restored": [{"path": t, "reason": AUDIT_REASON} for t in trails]}
    z.writestr(MANIFEST, json.dumps(manifest, indent=2))


def _has_sqlite_header(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False


# Files older backups carry that are now left out: restored by no one and quietly passed over.
NO_LONGER_BACKED_UP = {"app/link-port", "app/editor-port"}
NO_LONGER_BACKED_UP_DIRS = ("app/fabric-view/",)


def _no_longer(name: str) -> bool:
    return name in NO_LONGER_BACKED_UP or name.startswith(NO_LONGER_BACKED_UP_DIRS)


# A name in 8.3 shape (MSAL_C~1.BIN, PROJEC~1) may be a short name, which Windows reads as the long name it
# stands for, so restore refuses it and a backup leaves it out.
_SHORT_NAME = re.compile(r"[^.~]{0,6}~\d{1,6}(\.[^.]{0,3})?", re.IGNORECASE)


def _windows_alias(part: str) -> bool:
    """Whether Windows would read ``part`` as another file's name: it drops a trailing dot or space, keeps
    device names (NUL, COM1.txt) for the device, and reads a short name as its long name."""
    return (part != part.rstrip(". ") or part.split(".")[0].upper().rstrip(" ") in _DEVICE_NAMES
            or _SHORT_NAME.fullmatch(part) is not None)


def _safe(name: str) -> bool:
    path = PurePosixPath(name)
    # name == path.as_posix(): "app//x" would be "/x" once split at its first slash.
    return (name == path.as_posix() and not path.is_absolute() and ".." not in path.parts and "\\" not in name and ":" not in name
            and len(path.parts) >= 2 and path.parts[0] in ("app", "agent")
            and not any(_windows_alias(part) for part in path.parts)
            # Windows names ignore case: MSAL_CACHE.BIN is the token file msal_cache.bin
            and _skipped_any_case(PurePosixPath(*path.parts[1:])) is None)


def validate(zip_path: Path) -> tuple[dict[str, Any], list[str]]:
    """The backup's manifest and every reason not to restore it; nothing is written."""
    try:
        z = zipfile.ZipFile(zip_path)
    except _UNREADABLE as exc:  # a zip version it can't read raises NotImplementedError here
        return {}, [f"{zip_path} isn't a zip file: {exc}"]
    with z:
        try:
            if z.getinfo(MANIFEST).file_size > MAX_MANIFEST:
                return {}, [(f"Its manifest.json wasn't read, because it's over {MAX_MANIFEST // (1024 * 1024)} MB, far "
                            "larger than any backup's. Pick a backup KratosForge made.")]
            manifest = json.loads(z.read(MANIFEST))
        except (KeyError, ValueError, RecursionError, *_UNREADABLE):
            return {}, ["It has no readable manifest.json, so it isn't a KratosForge backup."]
        if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
            return {}, [f"It isn't a {SCHEMA} backup."]
        entries, cleaned = manifest.get("files") or [], manifest.get("cleaned") or []
        if (not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries)
                or not isinstance(manifest.get("created"), str) or not isinstance(cleaned, list)
                or not all(isinstance(c, dict) and {"path", "removed"} <= c.keys() for c in cleaned)):
            return {}, [("Its manifest.json can't be used, because a part of it is missing or isn't the kind of value "
                        "a backup writes. Make a new backup, or pick another one.")]
        problems = []
        listed: dict[str, Any] = {}
        lower: set[str] = set()
        kept = []
        for entry in entries:
            name = str(entry.get("path", ""))
            if _no_longer(name):
                continue
            kept.append(entry)
            if not _safe(name):
                problems.append(f"{name!r} would be written outside the app's folders or is never restored")
            if name.lower() in lower:  # Windows paths ignore case
                problems.append(f"{name} is listed twice in its manifest")
            listed[name] = entry
            lower.add(name.lower())
        for name in listed:  # restore stages each file as <name>.restoring beside it, in its folder
            parents = [p.as_posix().lower() for p in PurePosixPath(name).parents]
            if f"{name.lower()}.restoring" in lower or any(p in lower for p in parents):
                problems.append(f"{name} can't be restored, because another file in this backup would be written over "
                                "it, or it over that one. Make a new backup, or pick another one.")
        # Never restored, whatever the manifest says: every entry in an audit trail, by name.
        manifest = dict(manifest, files=kept, cleaned=cleaned, not_restored=[
            {"path": str(e.get("path", "")), "reason": AUDIT_REASON} for e in kept
            if _audit_trail(str(e.get("path", "")))])
        all_names = z.namelist()
        names = {n for n in all_names if not _no_longer(n)} - {MANIFEST}
        counts = Counter(all_names)
        problems += [f"{name} is in the zip twice" for name in sorted(n for n, c in counts.items() if c > 1)]
        for name in sorted(names - listed.keys()):
            problems.append(f"{name} is in the zip but not in its manifest")
        for name, entry in sorted(listed.items()):
            if name not in names:
                problems.append(f"{name} is in the manifest but missing from the zip")
            elif z.getinfo(name).file_size != entry.get("size"):  # checked before anything is read
                problems.append(f"{name} has changed since the backup was made")
            else:
                try:
                    if _zip_sha256(z, name) != entry.get("sha256"):
                        problems.append(f"{name} has changed since the backup was made")
                except (ValueError, *_UNREADABLE) as exc:  # damaged data, a bad CRC
                    problems.append(f"{name} can't be read from the zip: {exc}")
        return manifest, problems


def _zip_sha256(z: zipfile.ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with z.open(name) as src:
        for block in iter(lambda: src.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()



def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_database(name: str, z: zipfile.ZipFile) -> bool:
    if PurePosixPath(name).suffix.lower() in SQLITE_SUFFIXES:
        return True
    with z.open(name) as src:
        return src.read(len(SQLITE_HEADER)) == SQLITE_HEADER


def _in_use(path: Path) -> bool:
    """Whether another connection has the database at ``path`` open. Exclusive locking mode makes
    SQLite wait for every other connection, idle ones in WAL mode too, not only writers."""
    try:
        with path.open("rb") as f:
            if f.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                return False  # missing, empty or not a database: nothing can have it open as one
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        db = sqlite3.connect(path, timeout=0.5, isolation_level=None)
    except sqlite3.Error:
        return True
    try:
        no_checkpoint = getattr(sqlite3, "SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE", None)
        if no_checkpoint is not None and hasattr(db, "setconfig"):
            db.setconfig(no_checkpoint, True)  # leave its WAL alone, so the safety copy keeps it
        db.execute("PRAGMA locking_mode=EXCLUSIVE")
        db.execute("BEGIN EXCLUSIVE")
        db.execute("SELECT count(*) FROM sqlite_master").fetchall()
        db.execute("ROLLBACK")
        return False
    except sqlite3.Error as exc:
        # A damaged file isn't in use, it's what the restore is for: it is replaced, and its copy
        # kept in restore-safety. Anything else (busy, locked, can't read the file) counts as in use.
        return getattr(exc, "sqlite_errorcode", None) not in _DAMAGED
    finally:
        db.close()


_DAMAGED = (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)


def restore(zip_path: Path, data_dir: Path, agent_home: Path | None = None, apply: bool = False,
            now: datetime | None = None) -> dict[str, Any]:
    """What restoring ``zip_path`` writes; with ``apply`` it writes it, after copying each file it
    replaces, and moving a replaced database's ``-wal``/``-shm``/``-journal``, into
    ``restore-safety``. Without ``apply`` nothing changes.

    Every file is written beside its target and checked before any file here changes, and a restore
    that stops part way (a refused swap, a full disk) puts back the files it already swapped, so the
    folder is as it was or as the backup has it. If the PC itself stops part way, the files it had
    replaced are in ``restore-safety``."""
    manifest, problems = validate(zip_path)
    if problems:
        raise BackupError("This backup can't be restored:\n- " + "\n- ".join(problems))
    plan = []
    for entry in manifest["files"]:
        name = entry["path"]
        if _audit_trail(name):
            continue
        top, *rest = PurePosixPath(name).parts  # the parts validate checked: never the raw name
        base = agent_home if top == "agent" else data_dir
        if base is None:
            continue
        dest = base.joinpath(*rest)
        try:
            staging = dest.with_name(dest.name + ".restoring")  # the longer name, written first
            if _too_long(staging):  # Windows answers "not there" for such a name rather than refusing it
                raise OSError(errno.ENAMETOOLONG, os.strerror(errno.ENAMETOOLONG), str(staging))
            replaces = dest.exists()
        except OSError as exc:  # a path too long for this PC
            if refused := _unwritable(name, dest, exc):
                raise refused from exc
            raise
        plan.append({"path": name, "to": str(dest), "replaces": replaces})
    result: dict[str, Any] = {"created": manifest["created"], "files": plan, "applied": False,
                              "cleaned": manifest.get("cleaned", []), "not_restored": manifest["not_restored"],
                              "audit_entries_not_restored": _audit_entries(zip_path, manifest["not_restored"])}
    if not apply:
        return result
    hashes = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    safety = data_dir / SAFETY / stamp
    with zipfile.ZipFile(zip_path) as z:
        databases = {item["path"] for item in plan if _is_database(item["path"], z)}
        busy = [item["to"] for item in plan if item["path"] in databases and _in_use(Path(item["to"]))]
        if busy:  # checked for every database before any file changes
            raise BackupError("Nothing was changed: another program has these databases open:\n- "
                              + "\n- ".join(busy) + "\nClose KratosForge and stop the agent, "
                              "then run the restore again.")
        moved = False
        staged: list[Path] = []
        swapped: list[tuple[Path, Path | None, list[tuple[Path, Path]]]] = []
        try:
            # 1. Every file written beside its target and checked, before any file here changes.
            for item in plan:
                dest = Path(item["to"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".restoring")
                staged.append(tmp)
                with z.open(item["path"]) as src, open(tmp, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)  # streamed: a member's size is the backup's to claim
                    out.flush()
                    os.fsync(out.fileno())
                if _sha256(tmp) != hashes[item["path"]]:
                    raise BackupError(f"{item['path']} didn't write correctly to {dest}; nothing was changed.")
            # 2. A copy of every file it replaces, kept in restore-safety.
            for item in plan:
                if item["replaces"]:
                    keep = safety / item["path"]
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    with open(item["to"], "rb") as src, open(keep, "wb") as out:
                        shutil.copyfileobj(src, out, 1 << 20)
                        out.flush()
                        os.fsync(out.fileno())
            # 3. The swaps. If one fails, the files already swapped are put back as they were.
            for item, tmp in zip(plan, staged, strict=True):
                dest, keep = Path(item["to"]), safety / item["path"]
                aside: list[tuple[Path, Path]] = []
                swapped.append((dest, keep if item["replaces"] else None, aside))
                if item["path"] in databases:  # a stale WAL would bring back what the backup doesn't hold
                    for suffix in SQLITE_COMPANIONS:
                        companion = dest.with_name(dest.name + suffix)
                        if companion.exists():
                            keep.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(companion, keep.with_name(keep.name + suffix))
                            aside.append((companion, keep.with_name(keep.name + suffix)))
                            moved = True
                jsonfile.replace(tmp, dest)
        except BaseException as exc:
            back = _put_back(swapped, staged)
            refused = _unwritable(item["path"], Path(item["to"]), exc) if isinstance(exc, OSError) and back else None
            if refused:  # a folder in the way or a path this PC can't take: named, not a bare OSError
                raise refused from exc
            raise
    kept = moved or any(i["replaces"] for i in plan)
    result.update(applied=True, safety=str(safety) if kept else None, pruned=_prune_safety(data_dir / SAFETY, KEEP_SAFETY))
    return result


def _audit_entries(zip_path: Path, names: list[dict[str, str]]) -> int:
    """How many audit lines the backup's audit trails hold (its .jsonl files' lines)."""
    count = 0
    with zipfile.ZipFile(zip_path) as z:
        for item in names:
            if item["path"].lower().endswith(".jsonl"):
                with z.open(item["path"]) as src:
                    count += sum(1 for line in src if line.strip())
    return count


def _prune_safety(folder: Path, keep: int = KEEP_SAFETY) -> list[str]:
    """Delete all but the newest ``keep`` restores' copies in ``folder``, after a restore that worked.
    Only plain folders named as restore names them: restore never makes a link there, so one (a symbolic
    link or junction) is left alone rather than followed."""
    if not folder.is_dir():
        return []
    stamps = sorted((d for d in folder.iterdir() if _STAMP.fullmatch(d.name) and d.is_dir()
                     and not d.is_symlink() and not d.is_junction()), key=lambda d: d.name)
    gone = []
    for old in stamps[:-keep] if keep > 0 else stamps:
        try:
            trees.remove(old)
            gone.append(old.name)
        except OSError:
            _log.warning("restore: couldn't remove the old copies in %s", old, exc_info=True)
    return gone


def _too_long(path: str | Path) -> bool:
    """Whether a part of ``path`` is longer than the 255 characters (UTF-16 units) a Windows or Linux
    folder takes. Windows reports such a name as an invalid argument, or as not there, rather than as too long."""
    return any(len(part.encode("utf-16-le")) // 2 > 255 for part in Path(path).parts)


def _unwritable(name: str, dest: Path, exc: OSError) -> BackupError | None:
    """The restore refused by name when this PC's folder can't take ``name``; None for any other OSError."""
    if exc.errno == errno.ENAMETOOLONG or _too_long(exc.filename if isinstance(exc.filename, str) else dest):
        why = "its path is too long for this PC"
    elif dest.is_dir():
        why = "a folder is where the file goes"
    elif isinstance(exc, (FileExistsError, NotADirectoryError)):
        why = "a file is where one of its folders goes"
    else:
        return None
    return BackupError(f"Nothing was restored, because {name} can't be written to {dest} ({why}). Move what is in "
                       "the way, or restore into a folder with a shorter path, then run the restore again.")


def _put_back(swapped: list[tuple[Path, Path | None, list[tuple[Path, Path]]]], staged: list[Path]) -> bool:
    """Undo a restore that stopped part way: each file it swapped in goes back to its copy in
    restore-safety (or away, if the restore added it), with any database companions it moved, and no
    ``.restoring`` file is left. The copies stay in restore-safety. False when a file couldn't be put back."""
    back = True
    for dest, keep, aside in reversed(swapped):
        try:
            if dest.with_name(dest.name + ".restoring").exists():
                pass  # not swapped yet: only its companions moved
            elif keep is not None:
                jsonfile.write_bytes(dest, keep.read_bytes())
            else:
                dest.unlink(missing_ok=True)
            for companion, kept in aside:
                shutil.move(kept, companion)
        except OSError:
            back = False
            _log.warning("restore: couldn't put %s back; its copy is in restore-safety", dest, exc_info=True)
    for tmp in staged:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:  # the error that stopped the restore is the one to raise, not this
            _log.warning("restore: couldn't remove %s", tmp, exc_info=True)
    return back


def describe(result: dict[str, Any]) -> str:
    """A person-readable summary of ``restore``'s result."""
    out = io.StringIO()
    replaces = sum(1 for f in result["files"] if f["replaces"])
    verb = "Restored" if result["applied"] else "Would restore"
    out.write(f"{verb} {len(result['files'])} files from the backup of {result['created']} "
              f"({replaces} replace a file already here).\n")
    for item in result["cleaned"]:
        out.write(f"  {item['path']}: {item['removed']} was left out of the backup; set it again.\n")
    if result.get("not_restored"):
        trails = sorted({_audit_trail(n["path"]) or n["path"] for n in result["not_restored"]})
        count = result.get("audit_entries_not_restored", 0)
        out.write(f"Restore leaves the audit trails as they are: the backup's {', '.join(trails)} "
                  f"({count} audit {'entry' if count == 1 else 'entries'}) aren't restored.\n")
    if result.get("safety"):
        out.write(f"The files it replaced are in {result['safety']}.\n")
    if not result["applied"]:
        out.write("Nothing was changed. Close KratosForge, then run again with --yes to restore.\n")
    return out.getvalue()

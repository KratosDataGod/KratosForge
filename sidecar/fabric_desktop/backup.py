"""Backing up and restoring what this PC keeps for KratosForge (FD-126).

A backup is a zip of the app's folder (``%LOCALAPPDATA%\\FabricDesktop``): settings, notebooks,
schedules on this PC, run history, receipts and the local databases. With ``agent_home`` it also
holds a headless agent's queue and alert databases. It never holds:

- sign-in tokens (``msal_cache.bin``, the embedded Fabric pages' saved sign-in),
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

import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .redact import secrets
from . import jsonfile

SCHEMA = "fabric-desktop-backup@1"
MANIFEST = "manifest.json"
SAFETY = "restore-safety"  # where restore keeps the files it replaced
SKIP_DIRS = {"engine", "onelake-cache", "EBWebView", "logs", "schedule_locks", "_leases", SAFETY}  # top level only
SKIP_FILES = {"msal_cache.bin", "fabric-view-signin.bin"}
SKIP_SUFFIXES = {".log", ".tmp", ".lock", ".pyc"}
SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
SQLITE_HEADER = b"SQLite format 3\x00"
SQLITE_COMPANIONS = ("-wal", "-shm", "-journal")  # SQLite replays these onto the database they sit beside
TEXT_SUFFIXES = {".json", ".jsonl", ".toml", ".ipynb", ".sql", ".py", ".md", ".txt", ".csv", ".yaml", ".yml"}
SECRET_KEYS = {"teams_webhook", "platform_notify"}  # platform_notify holds webhook addresses
AGENT_FILES = ("queue.sqlite3", "alerts.sqlite3")


class BackupError(ValueError):
    pass


def _skipped(rel: PurePosixPath) -> str | None:
    if (len(rel.parts) > 1 and rel.parts[0] in SKIP_DIRS) or "__pycache__" in rel.parts:
        return "rebuilt by the app"
    if rel.name in SKIP_FILES:
        return "sign-in tokens are never backed up"
    if rel.suffix.lower() in SKIP_SUFFIXES or rel.name.endswith(SQLITE_COMPANIONS):
        return "rebuilt by the app"
    return None


def _sqlite_bytes(path: Path) -> bytes:
    """A consistent copy of a database that may be open, through SQLite's own backup."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "copy.sqlite"
        src = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        dst = sqlite3.connect(copy)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        return copy.read_bytes()


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
                if reason.startswith("sign-in"):
                    skipped.append({"path": f"app/{rel}", "reason": reason})
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
    entries, cleaned = [], []
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"fabric-desktop-backup-{now.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for name, path in files:
            rel = PurePosixPath(name)
            data = path.read_bytes()
            if rel.suffix.lower() in SQLITE_SUFFIXES:
                try:
                    data = _sqlite_bytes(path)
                except sqlite3.Error:
                    pass  # not a database after all: the file as it is
            if name == "app/schedules.json":
                data = _forget_engine(data)
            if rel.suffix.lower() in TEXT_SUFFIXES:
                data, removed = _clean_text(rel, data)
                if removed:
                    cleaned.append({"path": name, "removed": ", ".join(removed)})
            z.writestr(name, data)
            entries.append({"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        manifest = {"schema": SCHEMA, "created": now.isoformat(timespec="seconds"), "files": entries,
                    "cleaned": cleaned, "not_included": skipped}
        z.writestr(MANIFEST, json.dumps(manifest, indent=2))
    return {"path": str(target), "files": len(entries), "cleaned": cleaned}


def _safe(name: str) -> bool:
    path = PurePosixPath(name)
    return (not path.is_absolute() and ".." not in path.parts and "\\" not in name and ":" not in name
            and len(path.parts) >= 2 and path.parts[0] in ("app", "agent")
            and _skipped(PurePosixPath(*path.parts[1:])) is None)


def validate(zip_path: Path) -> tuple[dict[str, Any], list[str]]:
    """The backup's manifest and every reason not to restore it; nothing is written."""
    try:
        z = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        return {}, [f"{zip_path} isn't a zip file: {exc}"]
    with z:
        try:
            manifest = json.loads(z.read(MANIFEST))
        except (KeyError, ValueError):
            return {}, ["It has no readable manifest.json, so it isn't a KratosForge backup."]
        if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
            return {}, [f"It isn't a {SCHEMA} backup."]
        problems = []
        listed = {}
        for entry in manifest.get("files") or []:
            name = str(entry.get("path", ""))
            if not _safe(name):
                problems.append(f"{name!r} would be written outside the app's folders or is never restored")
            listed[name] = entry
        names = set(z.namelist()) - {MANIFEST}
        for name in sorted(names - listed.keys()):
            problems.append(f"{name} is in the zip but not in its manifest")
        for name, entry in sorted(listed.items()):
            if name not in names:
                problems.append(f"{name} is in the manifest but missing from the zip")
            elif hashlib.sha256(z.read(name)).hexdigest() != entry.get("sha256"):
                problems.append(f"{name} has changed since the backup was made")
        return manifest, problems


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
    except sqlite3.Error:
        return True
    finally:
        db.close()


def restore(zip_path: Path, data_dir: Path, agent_home: Path | None = None, apply: bool = False,
            now: datetime | None = None) -> dict[str, Any]:
    """What restoring ``zip_path`` writes; with ``apply`` it writes it, after copying each file it
    replaces, and moving a replaced database's ``-wal``/``-shm``/``-journal``, into
    ``restore-safety``. Without ``apply`` nothing changes."""
    manifest, problems = validate(zip_path)
    if problems:
        raise BackupError("This backup can't be restored:\n- " + "\n- ".join(problems))
    plan = []
    for entry in manifest["files"]:
        name = entry["path"]
        top, rest = name.split("/", 1)
        if top == "agent" and not agent_home:
            continue
        dest = (agent_home if top == "agent" else data_dir) / rest  # type: ignore[operator]
        plan.append({"path": name, "to": str(dest), "replaces": dest.exists()})
    result: dict[str, Any] = {"created": manifest["created"], "files": plan, "applied": False,
                              "cleaned": manifest.get("cleaned", [])}
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
        for item in plan:
            dest = Path(item["to"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".restoring")
            with z.open(item["path"]) as src:
                tmp.write_bytes(src.read())
            if _sha256(tmp) != hashes[item["path"]]:
                tmp.unlink()
                raise BackupError(f"{item['path']} didn't write correctly to {dest}; the restore stopped there. "
                                  f"Files it already replaced are in {safety}.")
            keep = safety / item["path"]
            if item["replaces"]:
                keep.parent.mkdir(parents=True, exist_ok=True)
                keep.write_bytes(dest.read_bytes())
            if item["path"] in databases:  # a stale WAL would bring back what the backup doesn't hold
                for suffix in SQLITE_COMPANIONS:
                    companion = dest.with_name(dest.name + suffix)
                    if companion.exists():
                        keep.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(companion, keep.with_name(keep.name + suffix))
                        moved = True
            jsonfile.replace(tmp, dest)
    kept = moved or any(i["replaces"] for i in plan)
    result.update(applied=True, safety=str(safety) if kept else None)
    return result


def describe(result: dict[str, Any]) -> str:
    """A person-readable summary of ``restore``'s result."""
    out = io.StringIO()
    replaces = sum(1 for f in result["files"] if f["replaces"])
    verb = "Restored" if result["applied"] else "Would restore"
    out.write(f"{verb} {len(result['files'])} files from the backup of {result['created']} "
              f"({replaces} replace a file already here).\n")
    for item in result["cleaned"]:
        out.write(f"  {item['path']}: {item['removed']} was left out of the backup; set it again.\n")
    if result.get("safety"):
        out.write(f"The files it replaced are in {result['safety']}.\n")
    if not result["applied"]:
        out.write("Nothing was changed. Close KratosForge, then run again with --yes to restore.\n")
    return out.getvalue()

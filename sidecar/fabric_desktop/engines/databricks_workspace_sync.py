"""Sync a folder of notebooks on this PC with a Databricks workspace folder (Preview; tested against
fakes, not a live workspace).

A pair is a local folder and a workspace folder. ``compare`` reads both (Workspace API ``list``, a
GET, so nothing starts) and says, file by file, what pushing would do. ``push`` writes with the
Workspace API as the signed-in person:

- **Never over someone else's change.** Each file remembers the workspace's ``modified_at`` from
  the last sync. Push lists the folder again and refuses a file whose workspace copy changed since:
  that's a conflict to compare and pick, not something to overwrite. A file that isn't in the
  workspace is written with ``overwrite`` false, so one that appeared meanwhile isn't replaced.
- **Never a delete without asking twice.** A file deleted on this PC is only deleted in the
  workspace when the person ticks it and push gets ``confirm_deletes``; folders are never deleted.
- **Git folders are read only here.** A workspace path under /Repos, or inside a Git folder, is
  refused: those change through Git.
- **Loss report.** A .ipynb goes across as Databricks source (``# COMMAND ----------``), and what that
  doesn't carry (outputs, the parameter cell, Fabric-only calls) is listed before anything is pushed.

Local names are plain file names in the folder: no subfolders, no "..", nothing a link points out of
the folder. Workspace paths are absolute and may not contain "..".
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

from .. import notebook_formats as nbf
from .. import redact
from ..jsonfile import read_json, write_json
from .databricks_discovery import _text
from .databricks_sql import Client

SCHEMA = "databricks-sync@1"
SUFFIXES = {".py": "PYTHON", ".ipynb": "PYTHON", ".sql": "SQL"}
MAX_FILES = 500
MAX_BYTES = 10 * 1024 * 1024    # the Workspace API's import limit
_REMOTE = re.compile(r"^/(Workspace/)?(Users|Shared)/[^\x00-\x1f]{1,1000}$")
_NAME = re.compile(r"^[A-Za-z0-9 ._()-]{1,200}$")


class SyncError(ValueError):
    """The pair or the push can't go ahead as asked; nothing was written."""


# ---------------------------------------------------------------- pairs


def check_remote(path: str) -> str:
    path = (path or "").rstrip("/")
    if not _REMOTE.fullmatch(path) or ".." in PurePosixPath(path).parts:
        raise SyncError("The workspace folder must be a full path under /Workspace/Users or /Workspace/Shared, "
                        "like /Workspace/Users/you@contoso.com/sales-etl.")
    if path.startswith(("/Repos", "/Workspace/Repos")):
        raise SyncError("That's a Git folder: it changes through Git, so it's read only here.")
    return path


def check_local(folder: str) -> Path:
    root = Path(folder or "")
    if not folder or not root.is_absolute() or not root.is_dir():
        raise SyncError("Pick a folder on this PC.")
    return root.resolve()


def _remote_name(local: str) -> str:
    return str(PurePosixPath(local).with_suffix(""))


class Pairs:
    """Pairs and each one's last-sync state, one JSON file on this PC."""

    def __init__(self, path: Path):
        self.path = path

    def all(self) -> dict[str, Any]:
        data = read_json(self.path, None)
        return data if isinstance(data, dict) and data.get("schema") == SCHEMA else {"schema": SCHEMA, "pairs": {}}

    def get(self, pair_id: str) -> dict[str, Any]:
        pair = self.all()["pairs"].get(pair_id)
        if not isinstance(pair, dict):
            raise SyncError("There's no such synced folder.")
        return pair

    def put(self, pair_id: str, pair: dict[str, Any]) -> None:
        data = self.all()
        data["pairs"][pair_id] = pair
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, data)

    def remove(self, pair_id: str) -> None:
        data = self.all()
        data["pairs"].pop(pair_id, None)
        write_json(self.path, data)


def new_pair(name: str, connection: str, local: str, remote: str) -> dict[str, Any]:
    if not _NAME.fullmatch(name or ""):
        raise SyncError("Name the pair with letters, digits, spaces and . _ ( ) - only.")
    return {"name": name, "connection": connection, "local": str(check_local(local)), "remote": check_remote(remote),
            "files": {}}


# ---------------------------------------------------------------- reading both sides


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def local_files(root: Path) -> dict[str, dict[str, Any]]:
    """Notebooks directly in ``root``: {name: {path, hash, bytes}}. A link that points out of the
    folder, a name with odd characters, and anything over the import limit are left out and listed."""
    out: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []
    for entry in sorted(os.scandir(root), key=lambda e: e.name)[:MAX_FILES * 2]:
        suffix = PurePosixPath(entry.name).suffix.lower()
        if suffix not in SUFFIXES or not entry.is_file(follow_symlinks=False):
            continue
        path = Path(entry.path)
        if not _NAME.fullmatch(entry.name) or path.resolve().parent != root:
            skipped.append(entry.name)
            continue
        size = entry.stat(follow_symlinks=False).st_size
        if size > MAX_BYTES:
            skipped.append(entry.name)
            continue
        out[entry.name] = {"path": path, "hash": _hash(path.read_bytes()), "bytes": size}
    if skipped:
        out["\x00skipped"] = {"names": skipped}
    return out


def _not_in_git_folder(client: Client, remote: str) -> bool:
    """Refuse a folder that is a Git folder or inside one, and say whether the folder exists. The
    folder and each parent below /Workspace/Users/<user> (or /Workspace/Shared) is read with
    get-status (GETs only); a missing one is skipped, since a Git folder can't be inside it."""
    from .databricks_sql import DatabricksError
    parts = PurePosixPath(remote).parts                      # ("/", "Workspace", "Users", "<user>", ...)
    top = 4 if parts[1:3] == ("Workspace", "Users") else 3   # the user's home or /Workspace/Shared
    exists = False
    for depth in range(len(parts), top, -1):
        try:
            reply = client.get("/api/2.0/workspace/get-status", {"path": str(PurePosixPath(*parts[:depth]))})
        except DatabricksError as exc:
            if "RESOURCE_DOES_NOT_EXIST" in str(exc) or "404" in str(exc):
                continue
            raise
        exists = exists or depth == len(parts)
        if reply.get("object_type") == "REPO":
            raise SyncError("That folder is a Git folder or inside one: it changes through Git, so it's read only here.")
    return exists


def remote_files(client: Client, remote: str) -> tuple[dict[str, dict[str, Any]], bool]:
    """Notebooks and files directly in the workspace folder: {name: {type, modified_at}}, and
    whether the folder exists. A Git folder (REPO), or a folder inside one, refuses."""
    if not _not_in_git_folder(client, remote):
        return {}, False
    out: dict[str, dict[str, Any]] = {}
    for obj in client.list("/api/2.0/workspace/list", "objects", {"path": remote}):
        if not isinstance(obj, dict) or obj.get("object_type") not in ("NOTEBOOK", "FILE"):
            continue
        name = PurePosixPath(str(obj.get("path") or "")).name
        if name:
            out[name] = {"type": obj["object_type"], "modified_at": int(obj.get("modified_at") or 0)}
    return out, True


def _remote_or_missing(client: Client, remote: str) -> tuple[dict[str, dict[str, Any]], bool]:
    from .databricks_sql import DatabricksError
    try:
        return remote_files(client, remote)
    except DatabricksError as exc:
        if "RESOURCE_DOES_NOT_EXIST" in str(exc) or "404" in str(exc):
            return {}, False
        raise


# ---------------------------------------------------------------- what would change


def compare(client: Client, pair: dict[str, Any]) -> dict[str, Any]:
    """File by file: this PC's state, the workspace's, and what push would do. Writes nothing."""
    root = check_local(pair["local"])
    remote = check_remote(pair["remote"])
    here = local_files(root)
    skipped = here.pop("\x00skipped", {}).get("names", [])
    there, exists = _remote_or_missing(client, remote)
    last = pair.get("files") or {}
    rows: list[dict[str, Any]] = []
    stems: dict[str, list[str]] = {}
    for name in here:
        stems.setdefault(_remote_name(name), []).append(name)
    for name, info in here.items():
        target = _remote_name(name)
        seen = last.get(name) or {}
        remote_now = there.get(target)
        local_state = "New" if not seen else ("Changed" if seen.get("hash") != info["hash"] else "Same")
        if remote_now is None:
            remote_state = "Not there" if not seen else "Deleted there"
        elif seen and remote_now["modified_at"] == seen.get("modified_at"):
            remote_state = "Same as last sync"
        else:
            remote_state = "Changed there" if seen else "Already there"
        if len(stems[target]) > 1:
            action, note = "blocked", f"{', '.join(stems[target])} would both be {target} in the workspace: rename one."
        elif remote_state in ("Changed there", "Already there"):
            action, note = "conflict", "Changed in Databricks too: compare and pick before pushing."
        elif local_state == "Same" and remote_state == "Same as last sync":
            action, note = "none", ""
        elif remote_state == "Deleted there":
            action, note = "push", "Deleted in Databricks since the last sync: pushing puts it back."
        else:
            action, note = "push", ""
        losses = []
        if action == "push" and name.endswith(".ipynb"):
            losses = [n["what"] for n in _loss_notes(info["path"])]
        rows.append({"file": name, "remote": target, "here": local_state, "there": remote_state, "action": action,
                     "note": note, "losses": losses})
    for name in last:
        if name in here:
            continue
        target = _remote_name(name)
        if target not in there:
            continue
        if there[target]["modified_at"] == (last[name] or {}).get("modified_at"):
            rows.append({"file": name, "remote": target, "here": "Deleted", "there": "Still there", "action": "delete",
                         "note": "Off unless you tick it: deleting asks again.", "losses": []})
        else:
            rows.append({"file": name, "remote": target, "here": "Deleted", "there": "Changed there", "action": "conflict",
                         "note": "Deleted here but changed in Databricks since the last sync: it isn't deleted.", "losses": []})
    return {"schema": SCHEMA, "remote_exists": exists, "rows": rows, "skipped": skipped,
            "compared_at": int(time.time())}


def _loss_notes(path: Path) -> list[dict[str, Any]]:
    try:
        cells = nbf.parse_ipynb(path.read_text(encoding="utf-8"))["cells"]
    except (OSError, ValueError, UnicodeDecodeError):
        return [{"what": "This notebook can't be read, so it isn't pushed."}]
    return nbf.databricks_export_notes(cells)


def _content(path: Path) -> tuple[str, str]:
    """(base64 source, language) to import: a .ipynb becomes Databricks Python source."""
    language = SUFFIXES[path.suffix.lower()]
    if path.suffix.lower() == ".ipynb":
        cells = nbf.parse_ipynb(path.read_text(encoding="utf-8"))["cells"]
        text = nbf.to_databricks_py(cells)
    else:
        text = path.read_text(encoding="utf-8")
    return base64.b64encode(text.encode("utf-8")).decode("ascii"), language


# ---------------------------------------------------------------- push


def push(client: Client, pair: dict[str, Any], files: list[str], deletes: list[str] | None = None,
         confirm_deletes: bool = False) -> dict[str, Any]:
    """Push ``files`` (and delete ``deletes`` in the workspace, only with ``confirm_deletes``). Compares
    again first: a file that isn't a "push" now (a conflict appeared, say) is skipped and says why.
    Returns what was written and the pair's new last-sync state."""
    deletes = list(deletes or [])
    if deletes and not confirm_deletes:
        raise SyncError("Deleting in Databricks needs a second confirm. Nothing was pushed.")
    now = compare(client, pair)
    by_file = {r["file"]: r for r in now["rows"]}
    remote = check_remote(pair["remote"])
    root = check_local(pair["local"])
    done: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    state = dict(pair.get("files") or {})
    if not now["remote_exists"] and files:
        client.workspace_mkdirs(remote)
    for name in files:
        row = by_file.get(name)
        if row is None or row["action"] != "push":
            skipped.append({"file": name, "why": (row or {}).get("note") or "Nothing to push."})
            continue
        path = root / name
        if path.resolve().parent != root:
            skipped.append({"file": name, "why": "Not a file in this folder."})
            continue
        try:
            content, language = _content(path)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            skipped.append({"file": name, "why": redact.secrets(_text(exc))})
            continue
        target = f"{remote}/{row['remote']}"
        existed = row["there"] != "Not there" and row["there"] != "Deleted there"
        client.workspace_import({"path": target, "format": "SOURCE", "language": language, "content": content,
                                 "overwrite": existed})
        done.append({"file": name, "action": "pushed", "path": target})
    for name in deletes:
        row = by_file.get(name)
        if row is None or row["action"] != "delete":
            skipped.append({"file": name, "why": "Not a file deleted on this PC and still in Databricks."})
            continue
        target = f"{remote}/{row['remote']}"
        client.workspace_delete(target)
        state.pop(name, None)
        done.append({"file": name, "action": "deleted", "path": target})
    if done:
        after, _ = remote_files(client, remote)
        here = local_files(root)
        for d in done:
            if d["action"] == "pushed":
                state[d["file"]] = {"hash": here[d["file"]]["hash"],
                                    "modified_at": (after.get(_remote_name(d["file"])) or {}).get("modified_at", 0)}
    return {"done": done, "skipped": skipped, "files": state}


def pull(client: Client, pair: dict[str, Any], files: list[str]) -> dict[str, Any]:
    """Bring workspace changes to this PC for files unchanged here since the last sync (a GET each).
    A file changed on both sides is a conflict and isn't touched."""
    now = compare(client, pair)
    by_file = {r["file"]: r for r in now["rows"]}
    root = check_local(pair["local"])
    remote = check_remote(pair["remote"])
    state = dict(pair.get("files") or {})
    done, skipped = [], []
    there, _ = remote_files(client, remote)
    for name in files:
        row = by_file.get(name)
        if row is None or row["here"] != "Same" or row["there"] != "Changed there" or not name.endswith((".py", ".sql")):
            skipped.append({"file": name, "why": "Only a .py or .sql file changed in Databricks and not here is pulled."})
            continue
        reply = client.get("/api/2.0/workspace/export", {"path": f"{remote}/{row['remote']}", "format": "SOURCE"})
        encoded = str(reply.get("content") or "")
        if len(encoded) > MAX_BYTES * 4 // 3 + 4:
            skipped.append({"file": name, "why": "It's over 10 MB in Databricks, so it isn't pulled."})
            continue
        data = base64.b64decode(encoded, validate=True)
        path = root / name
        if path.is_symlink() or path.resolve().parent != root:
            skipped.append({"file": name, "why": "Not a file in this folder."})
            continue
        # A new file of its own (never one a link points at), swapped in whole.
        fd, tmp = tempfile.mkstemp(dir=root, prefix=".kf-pull-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        state[name] = {"hash": _hash(data), "modified_at": there.get(row["remote"], {}).get("modified_at", 0)}
        done.append({"file": name, "action": "pulled"})
    return {"done": done, "skipped": skipped, "files": state}

"""Version history for notebooks on this PC: a version on each save, named checkpoints, compare and restore.

Versions are kept in the app's data folder (``root/notebook-versions/<notebook name>/``), outside
every notebooks repository, so they never show as changes in Git. Each version is one JSON file,
``{id, at, kind, label, hash, nb}``, where ``nb`` is the notebook file (.ipynb JSON) as it was saved.

Saving every few seconds (autosave) would make hundreds of versions, so automatic versions are
coalesced: a save within ``COALESCE_MINUTES`` of the last automatic version replaces it. Automatic
versions are kept ``KEEP_DAYS`` days and at most ``MAX_AUTO``; checkpoints are kept until deleted.

``index.json`` beside the versions holds each one's summary without the notebook, so a save and the
list read one small file rather than every version (a long history can be hundreds of MB). It is
rebuilt from the version files whenever it is missing, unreadable or names other files than are there."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .jsonfile import write_json
from .meta import diff as diff_mod
from .notebook import check_notebook_name, from_ipynb
from . import jsonfile

COALESCE_MINUTES = 5
KEEP_DAYS = 30
MAX_AUTO = 100
MAX_CHECKPOINTS = 200
LABEL_CHARS = 80
KINDS = ("auto", "checkpoint", "before restore", "changed outside", "not saved")
_ID = re.compile(r"^[0-9]{14}-[0-9a-f]{8}$")
INDEX = "index.json"
_ENTRY = ("id", "t", "at", "kind", "label", "hash", "cells", "code")


def content_hash(nb: dict[str, Any]) -> str:
    """What a version is compared by: each cell's type and source (outputs and metadata aside)."""
    cells = [[c.get("cell_type"), "".join(c.get("source") or "") if isinstance(c.get("source"), list) else c.get("source") or ""]
             for c in nb.get("cells") or []]
    return hashlib.sha256(json.dumps(cells, separators=(",", ":")).encode()).hexdigest()


def _now() -> float:
    return time.time()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _folder(root: Path, name: str) -> Path:
    return Path(root) / check_notebook_name(name)


def _check_id(vid: str) -> str:
    if not isinstance(vid, str) or not _ID.fullmatch(vid):
        raise ValueError("That isn't a version of this notebook.")
    return vid


def _write(path: Path, record: dict[str, Any]) -> None:
    jsonfile.write_text(path, json.dumps(record))


def _read(path: Path) -> dict[str, Any] | None:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # a torn or unreadable version is skipped, never shown as the notebook
    ok = (isinstance(rec, dict) and isinstance(rec.get("nb"), dict) and isinstance(rec.get("id"), str)
          and _ID.fullmatch(rec["id"]) and isinstance(rec.get("t"), (int, float)) and rec.get("kind") in KINDS
          and isinstance(rec.get("hash"), str) and isinstance(rec.get("at"), str))
    return rec if ok else None  # a record missing a field (hand-made, or from another build) is skipped


def _all(root: Path, name: str) -> list[dict[str, Any]]:
    """Every version in full (notebook included), newest first. Only a rebuild of the index needs this."""
    folder = _folder(root, name)
    if not folder.is_dir():
        return []
    out = []
    for p in folder.glob("*.json"):
        if _ID.fullmatch(p.stem) and (rec := _read(p)) and rec["id"] == p.stem:
            out.append(rec)
    return sorted(out, key=lambda r: (r.get("t", 0), r["id"]), reverse=True)


def _entry(rec: dict[str, Any]) -> dict[str, Any]:
    cells = rec["nb"].get("cells") or []
    return {"id": rec["id"], "t": rec["t"], "at": rec["at"], "kind": rec["kind"], "label": rec.get("label") or "",
            "hash": rec["hash"], "cells": len(cells), "code": sum(c.get("cell_type") == "code" for c in cells)}


def _ok_entry(e: Any) -> bool:
    return (isinstance(e, dict) and isinstance(e.get("id"), str) and bool(_ID.fullmatch(e["id"]))
            and isinstance(e.get("t"), (int, float)) and e.get("kind") in KINDS and isinstance(e.get("hash"), str)
            and isinstance(e.get("at"), str) and isinstance(e.get("cells"), int) and isinstance(e.get("code"), int))


def _ids_on_disk(folder: Path) -> set[str]:
    return {p.stem for p in folder.glob("*.json") if _ID.fullmatch(p.stem)}


def _save_index(folder: Path, entries: list[dict[str, Any]]) -> None:
    write_json(folder / INDEX, {"versions": entries}, indent=0)


def _entries(root: Path, name: str) -> list[dict[str, Any]]:
    """Each version's summary, newest first, from the index; rebuilt from the version files when the
    index is missing, unreadable, or doesn't name exactly the version files in the folder."""
    folder = _folder(root, name)
    if not folder.is_dir():
        return []
    on_disk = _ids_on_disk(folder)
    try:
        listed = json.loads((folder / INDEX).read_text(encoding="utf-8")).get("versions")
    except (OSError, ValueError, AttributeError):
        listed = None
    if (isinstance(listed, list) and all(_ok_entry(e) for e in listed)
            and len(listed) == len(on_disk) and {e["id"] for e in listed} == on_disk):
        return sorted(listed, key=lambda e: (e["t"], e["id"]), reverse=True)
    entries = [_entry(r) for r in _all(root, name)]
    try:
        _save_index(folder, entries)
    except OSError:
        pass  # read-only folder: the list still works, rebuilt each time
    return entries


def _summary(entry: dict[str, Any], current: str | None) -> dict[str, Any]:
    return {"id": entry["id"], "at": entry["at"], "kind": entry["kind"], "label": entry.get("label") or "",
            "cells": entry["cells"], "code": entry["code"], "current": entry["hash"] == current}


def record(root: Path, name: str, nb: dict[str, Any], kind: str = "auto", label: str = "",
           now: float | None = None) -> dict[str, Any] | None:
    """Keeps ``nb`` (the notebook file's JSON) as a version. An automatic version that matches the
    newest version is skipped, and one within COALESCE_MINUTES of the last automatic version
    replaces it. Returns the version's summary, or None when nothing was kept."""
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    label = " ".join((label or "").split())[:LABEL_CHARS]
    if kind == "checkpoint" and not label:
        raise ValueError("Name the checkpoint.")
    t = _now() if now is None else now
    h = content_hash(nb)
    versions = _entries(root, name)
    if versions and t <= versions[0]["t"]:
        t = versions[0]["t"] + 0.001  # two in the same instant (a checkpoint right after a save) keep their order
    folder = _folder(root, name)
    if kind == "checkpoint" and sum(v["kind"] == "checkpoint" for v in versions) >= MAX_CHECKPOINTS:
        raise ValueError(f"This notebook has {MAX_CHECKPOINTS} checkpoints. Delete some before adding more.")
    if kind == "auto" and versions and versions[0]["hash"] == h:
        return None
    replace = None
    if kind == "auto":
        last_auto = next((v for v in versions if v["kind"] == "auto"), None)
        if last_auto and versions[0] is last_auto and t - last_auto["t"] < COALESCE_MINUTES * 60:
            replace = last_auto
    vid = f"{datetime.fromtimestamp(t, timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    rec = {"id": vid, "t": t, "at": _iso(t), "kind": kind, "label": label, "hash": h, "nb": nb}
    _write(folder / f"{vid}.json", rec)
    entries = [_entry(rec)] + versions
    if replace:
        (folder / f"{replace['id']}.json").unlink(missing_ok=True)
        entries = [e for e in entries if e["id"] != replace["id"]]
    entries = _prune(folder, entries, t)
    _save_index(folder, entries)
    return _summary(entries[0], h)


def _prune(folder: Path, entries: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """Removes automatic versions over MAX_AUTO or older than KEEP_DAYS; returns the ones kept."""
    kept, autos = [], 0
    for e in entries:
        if e["kind"] != "checkpoint":
            autos += 1
            if autos > MAX_AUTO or now - e["t"] > KEEP_DAYS * 86400:
                (folder / f"{e['id']}.json").unlink(missing_ok=True)
                continue
        kept.append(e)
    return kept


def listing(root: Path, name: str, current_nb: dict[str, Any] | None) -> dict[str, Any]:
    current = content_hash(current_nb) if current_nb else None
    return {"name": name, "versions": [_summary(v, current) for v in _entries(root, name)],
            "keep_days": KEEP_DAYS, "coalesce_minutes": COALESCE_MINUTES}


def get(root: Path, name: str, vid: str) -> dict[str, Any]:
    path = _folder(root, name) / f"{_check_id(vid)}.json"
    rec = _read(path) if path.is_file() else None
    if not rec:
        raise ValueError("That version isn't kept any more.")
    return rec


def compare(old_nb: dict[str, Any], new_nb: dict[str, Any]) -> list[dict[str, Any]]:
    return diff_mod.notebook_changes(old_nb, new_nb)


def as_notebook(rec: dict[str, Any]) -> dict[str, Any]:
    """The version as the notebook screen shows it: cells, lakehouse, compute, workspace."""
    return from_ipynb(rec["nb"])


def delete(root: Path, name: str, vid: str) -> None:
    folder = _folder(root, name)
    entries = _entries(root, name)
    (folder / f"{_check_id(vid)}.json").unlink(missing_ok=True)
    if folder.is_dir():
        _save_index(folder, [e for e in entries if e["id"] != vid])


def keep_copy(root: Path, name: str, src: Path) -> Path:
    """A byte copy of a notebook's file that isn't a notebook the app can read any more (a Git merge
    left conflict markers in it), kept beside its versions as ``outside-<time>.ipynb``."""
    folder = _folder(root, name)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"outside-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}.ipynb"
    shutil.copyfile(src, target)
    return target


def rename(root: Path, name: str, new_name: str) -> None:
    """Versions follow a renamed notebook. When versions are already kept under the new name (an
    earlier notebook of that name), both sets are kept together; ids never clash."""
    src, dest = _folder(root, name), _folder(root, new_name)
    if not src.is_dir():
        return
    # A notebook "Sales" and a folder "Sales" (notebook_folders) share this folder: the versions of
    # the notebooks in it (subfolders here) stay, and only this notebook's own files move.
    if dest.exists() or dest.is_relative_to(src) or any(p.is_dir() for p in src.iterdir()):
        dest.mkdir(parents=True, exist_ok=True)
        for p in [*src.glob("*.json"), *src.glob("outside-*.ipynb")]:
            target = dest / p.name
            if not target.exists():
                shutil.move(str(p), str(target))
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)

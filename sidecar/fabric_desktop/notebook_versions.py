"""Version history for notebooks on this PC: a version on each save, named checkpoints, compare and restore.

Versions are kept in the app's data folder (``root/notebook-versions/<notebook name>/``), outside
every notebooks repository, so they never show as changes in Git. Each version is one JSON file,
``{id, at, kind, label, hash, nb}``, where ``nb`` is the notebook file (.ipynb JSON) as it was saved.

Saving every few seconds (autosave) would make hundreds of versions, so automatic versions are
coalesced: a save within ``COALESCE_MINUTES`` of the last automatic version replaces it. Automatic
versions are kept ``KEEP_DAYS`` days and at most ``MAX_AUTO``; checkpoints are kept until deleted."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .meta import diff as diff_mod
from .notebook import check_notebook_name, from_ipynb

COALESCE_MINUTES = 5
KEEP_DAYS = 30
MAX_AUTO = 100
MAX_CHECKPOINTS = 200
LABEL_CHARS = 80
KINDS = ("auto", "checkpoint", "before restore")
_ID = re.compile(r"^[0-9]{14}-[0-9a-f]{8}$")


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
    if not isinstance(vid, str) or not _ID.match(vid):
        raise ValueError("That isn't a version of this notebook.")
    return vid


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # a torn or unreadable version is skipped, never shown as the notebook
    return rec if isinstance(rec, dict) and isinstance(rec.get("nb"), dict) else None


def _all(root: Path, name: str) -> list[dict[str, Any]]:
    folder = _folder(root, name)
    if not folder.is_dir():
        return []
    out = []
    for p in folder.glob("*.json"):
        if _ID.match(p.stem) and (rec := _read(p)):
            out.append(rec)
    return sorted(out, key=lambda r: (r.get("t", 0), r["id"]), reverse=True)


def _summary(rec: dict[str, Any], current: str | None) -> dict[str, Any]:
    cells = rec["nb"].get("cells") or []
    return {"id": rec["id"], "at": rec["at"], "kind": rec["kind"], "label": rec.get("label") or "",
            "cells": len(cells), "code": sum(c.get("cell_type") == "code" for c in cells),
            "current": rec.get("hash") == current}


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
    versions = _all(root, name)
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
    if replace:
        (folder / f"{replace['id']}.json").unlink(missing_ok=True)
    _prune(root, name, t)
    return _summary(rec, h)


def _prune(root: Path, name: str, now: float) -> None:
    autos = [v for v in _all(root, name) if v["kind"] != "checkpoint"]
    folder = _folder(root, name)
    for i, v in enumerate(autos):
        if i >= MAX_AUTO or now - v["t"] > KEEP_DAYS * 86400:
            (folder / f"{v['id']}.json").unlink(missing_ok=True)


def listing(root: Path, name: str, current_nb: dict[str, Any] | None) -> dict[str, Any]:
    current = content_hash(current_nb) if current_nb else None
    return {"name": name, "versions": [_summary(v, current) for v in _all(root, name)],
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
    (_folder(root, name) / f"{_check_id(vid)}.json").unlink(missing_ok=True)


def rename(root: Path, name: str, new_name: str) -> None:
    """Versions follow a renamed notebook. When versions are already kept under the new name (an
    earlier notebook of that name), both sets are kept together; ids never clash."""
    src, dest = _folder(root, name), _folder(root, new_name)
    if not src.is_dir():
        return
    if dest.exists():
        for p in src.glob("*.json"):
            target = dest / p.name
            if not target.exists():
                shutil.move(str(p), str(target))
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)

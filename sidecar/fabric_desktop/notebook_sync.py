"""Merging a notebook changed in two places before a sync: in KratosForge and in another program,
such as VS Code, that saved the same file.

Cells are matched by their cell id against the base, the cells both sides last agreed on (what
the app read or saved, kept by its disk stamp in SyncBases). A change made on one side only is
taken as it is. A cell changed on both sides in different ways, a cell deleted on one side and
changed on the other, and cells reordered differently on both sides are named conflicts that
carry both versions: nothing is picked for the person, and nothing is dropped until they choose.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from . import jsonfile

BOTH_EDITED = "both_edited"
DELETED_HERE = "deleted_here"      # deleted in KratosForge, changed in the other program
DELETED_THERE = "deleted_there"    # deleted in the other program, changed in KratosForge
REORDERED = "reordered_both"
KINDS = (BOTH_EDITED, DELETED_HERE, DELETED_THERE, REORDERED)
CHOICES = ("mine", "theirs", "both")
ORDER_CONFLICT_ID = "order"
THEIRS_TITLE = "From VS Code"
KEEP_BASES = 4  # per notebook: the stamps the app may still hold, newest first

_NAMES = {
    BOTH_EDITED: "Changed in both places",
    DELETED_HERE: "Deleted in KratosForge, changed in the other program",
    DELETED_THERE: "Deleted in the other program, changed in KratosForge",
    REORDERED: "Cells moved in both places",
}


def _content(cell: dict[str, Any] | None) -> Any:
    """What makes two versions of a cell the same: its type, source and the settings the app reads.
    Outputs are left out, so outputs that arrived on one side aren't a change to the cell."""
    if cell is None:
        return None
    return [cell.get("type", "code"), cell.get("source", ""), cell.get("meta") or {}]


def _by_id(cells: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for c in cells:
        cid = c.get("cell_id")
        if not isinstance(cid, str) or not cid:
            raise ValueError("Every cell needs its cell id to be merged.")
        if cid in out:
            raise ValueError(f"Two cells share the id {cid!r}.")
        out[cid] = c
    return out


def _moved(order: list[str], common: set[str]) -> list[str]:
    """The cells on both sides and in the base, in this side's order; compared with the base's."""
    return [i for i in order if i in common]


def _place(primary: list[str], other: list[str], keep: set[str]) -> list[str]:
    """``primary``'s order, with each cell only ``other`` has put after the cell it follows there."""
    seq = [i for i in primary if i in keep]
    placed = set(seq)
    for n, cid in enumerate(other):
        if cid in placed or cid not in keep:
            continue
        before = next((other[k] for k in range(n - 1, -1, -1) if other[k] in placed), None)
        seq.insert(seq.index(before) + 1 if before is not None else 0, cid)
        placed.add(cid)
    return seq


def _copy_id(cid: str, taken: set[str]) -> str:
    """An id for the second copy of a cell kept both ways: valid as an nbformat id, and unused."""
    stem = re.sub(r"[^A-Za-z0-9_-]", "", cid)[:40] or "cell"
    for n in range(1, 1000):
        new = f"{stem}-theirs{'' if n == 1 else n}"
        if new not in taken:
            return new
    return uuid.uuid4().hex[:16]


def merge_cells(base: list[dict[str, Any]], mine: list[dict[str, Any]], theirs: list[dict[str, Any]],
                choices: dict[str, str] | None = None) -> dict[str, Any]:
    """Three-way merge of a notebook's cells. ``mine`` is KratosForge's, ``theirs`` is the file as
    the other program saved it, ``base`` what both started from.

    Returns {"cells", "conflicts", "resolved"}. Each conflict is {"id", "kind", "name", "cell_id",
    "base", "mine", "theirs"} (the order conflict carries the two orders of cell ids instead). A
    conflict ``choices`` names ("mine", "theirs", or "both" for an edited cell: mine, then theirs as
    a new cell after it, titled THEIRS_TITLE when it has no title) is applied and not listed. Until
    every conflict is chosen, ``cells`` holds both versions of each conflicted cell (the same way, and a cell
    deleted on one side kept), so a save of it would lose nothing; ``resolved`` is False."""
    choices = dict(choices or {})
    for key, pick in choices.items():
        if pick not in CHOICES:
            raise ValueError(f"{key}: choose one of {', '.join(CHOICES)}")
    b, m, t = _by_id(base), _by_id(mine), _by_id(theirs)
    b_order, m_order, t_order = list(b), list(m), list(t)
    conflicts: list[dict[str, Any]] = []
    chosen: dict[str, dict[str, Any]] = {}   # cell id -> the cell kept
    extra: dict[str, dict[str, Any]] = {}    # cell id -> theirs, kept as a new cell after mine
    keep: set[str] = set()

    def conflict(kind: str, cid: str) -> None:
        pick = choices.get(cid)
        if pick == "both" and kind != BOTH_EDITED:
            raise ValueError(f"{cid}: a deleted cell is kept or deleted; choose mine or theirs")
        if pick is None:
            conflicts.append({"id": cid, "kind": kind, "name": _NAMES[kind], "cell_id": cid,
                              "base": b.get(cid), "mine": m.get(cid), "theirs": t.get(cid)})
            pick = "both"
        if kind == BOTH_EDITED:
            chosen[cid] = t[cid] if pick == "theirs" else m[cid]
            if pick == "both":
                extra[cid] = t[cid]
            keep.add(cid)
        else:
            side = m if pick == "mine" else t
            if pick == "both":  # not chosen yet: keep the changed version
                side = t if kind == DELETED_HERE else m
            if cid in side:
                chosen[cid] = side[cid]
                keep.add(cid)

    for cid in dict.fromkeys(b_order + t_order + m_order):
        bc, mc, tc = b.get(cid), m.get(cid), t.get(cid)
        if bc is None:  # added since the base
            if mc is not None and tc is not None and _content(mc) != _content(tc):
                conflict(BOTH_EDITED, cid)
            else:
                chosen[cid] = mc if mc is not None else tc  # type: ignore[assignment]
                keep.add(cid)
            continue
        m_changed = mc is not None and _content(mc) != _content(bc)
        t_changed = tc is not None and _content(tc) != _content(bc)
        if mc is not None and tc is not None:
            if m_changed and t_changed and _content(mc) != _content(tc):
                conflict(BOTH_EDITED, cid)
            else:
                chosen[cid] = mc if m_changed else tc
                keep.add(cid)
        elif mc is None and tc is None:
            continue  # deleted on both sides
        elif mc is None:  # deleted in KratosForge
            if t_changed:
                conflict(DELETED_HERE, cid)
        elif m_changed:  # deleted in the other program
            conflict(DELETED_THERE, cid)

    common = set(b) & set(m) & set(t)
    b_rel, m_rel, t_rel = (_moved(o, common) for o in (b_order, m_order, t_order))
    m_moved, t_moved = m_rel != b_rel, t_rel != b_rel
    primary, other = m_order, t_order
    if m_moved and t_moved and m_rel != t_rel:
        order_pick = choices.get(ORDER_CONFLICT_ID)
        if order_pick not in ("mine", "theirs"):
            if order_pick == "both":
                raise ValueError("order: choose mine or theirs")
            conflicts.append({"id": ORDER_CONFLICT_ID, "kind": REORDERED, "name": _NAMES[REORDERED], "cell_id": None,
                              "base": b_rel, "mine": m_rel, "theirs": t_rel})
            order_pick = "theirs"  # shown in the file's order until the person chooses
        if order_pick == "theirs":
            primary, other = t_order, m_order
    elif t_moved and not m_moved:
        primary, other = t_order, m_order
    order = _place(primary, other, keep)
    # A kept cell the leading side deleted is placed after its neighbour on the other side.
    cells: list[dict[str, Any]] = []
    taken = set(order)
    for cid in order:
        cells.append(chosen[cid])
        if cid in extra:
            new = _copy_id(cid, taken)
            taken.add(new)
            copy = dict(extra[cid], cell_id=new)
            meta = dict(copy.get("meta") or {})
            if not str(meta.get("title") or "").strip():
                meta["title"] = THEIRS_TITLE  # so the cell kept from the other side says where it came from
            copy["meta"] = meta
            cells.append(copy)
    return {"cells": cells, "conflicts": conflicts, "resolved": not conflicts}


_now = time.time_ns  # a test freezes it
_utime = os.utime  # and keeps times to a file system's tick


def _make_newest(folder: Path, path: Path) -> None:
    """Gives path a file time later than every other base in folder. Pruning keeps the newest few by
    file time, and two quick writes can share one (coarse clocks, Windows), so the order is made strict.
    The stamp can't order them: it is a content hash. The file system keeps times to its own tick (100 ns
    on NTFS, 2 s on FAT), so the step grows until the time read back is later than every other one."""
    latest = max((p.stat().st_mtime_ns for p in folder.glob("*.json") if p != path), default=None)
    now = _now()
    try:
        if latest is None or now > latest:
            _utime(path, ns=(now, now))
            if latest is None or path.stat().st_mtime_ns > latest:
                return
        step = 100
        while step <= 4_000_000_000:  # past FAT's 2 s tick
            t = max(now, latest + step)
            _utime(path, ns=(t, t))
            if path.stat().st_mtime_ns > latest:
                return
            step *= 2
    except (OSError, OverflowError):  # a base set far in the future, past what the file system holds
        _utime(path, ns=(now, now))


class SyncBases:
    """The cells the app last read or saved for each notebook, by disk stamp: the base a merge
    starts from. Kept on disk, so a sync cut short by closing the app can still be merged after
    it starts again. A file that can't be read (cut short mid-write) is no base, never an error."""

    def __init__(self, root: Path):
        self.root = root

    def _folder(self, name: str) -> Path:
        return self.root / hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]

    def remember(self, name: str, stamp: str, cells: list[dict[str, Any]]) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", stamp or ""):
            return  # a raw stamp of an unreadable file is never a base
        folder = self._folder(name)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stamp}.json"
        keep = [{k: c[k] for k in ("cell_id", "type", "source", "meta") if k in c} for c in cells]
        # Opened again unchanged: already kept. Compared in full, since the stamp leaves cell ids out.
        if self.find(name, stamp) == keep:
            _make_newest(folder, path)  # newest again, so it isn't the one pruned
            return
        tmp = folder / f"{stamp}.{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"name": name, "cells": keep}, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            jsonfile.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        _make_newest(folder, path)
        old = sorted((p for p in folder.glob("*.json")), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for p in old[KEEP_BASES:]:
            p.unlink(missing_ok=True)

    def find(self, name: str, stamp: str | None) -> list[dict[str, Any]] | None:
        if not stamp or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", stamp):
            return None
        path = self._folder(name) / f"{stamp}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("name") != name or not isinstance(data.get("cells"), list):
            return None
        return data["cells"]

    def forget(self, name: str) -> None:
        """Drops the notebook's bases (renamed or deleted: no copies of its cells are left behind).
        A merge after that asks for the whole notebook, as when no base is known."""
        folder = self._folder(name)
        for p in folder.glob("*"):
            p.unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            pass  # not there, or a write landed meanwhile: left for the next forget

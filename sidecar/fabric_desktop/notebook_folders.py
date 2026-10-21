"""Folders for notebooks: group notebooks into folders inside a place (the app's notebooks folder,
or a notebooks repository's ``notebooks`` folder), as Fabric and Databricks workspace folders do.

A folder is part of the notebook's name: "Sales/Daily load" is ``Sales/Daily load.ipynb`` in the
place's folder, with its resources in ``Sales/Daily load.resources/`` beside it. Folders go
``MAX_FOLDER_DEPTH`` levels deep and each part passes the notebook name rule (notebook.py
``check_notebook_name``). Everything kept by a notebook's name (versions, Recently deleted, this
PC's schedules, its session, environment and Fabric link) uses the whole name, so it follows a
notebook moved into a folder the way it follows a rename.

Nothing here replaces or deletes a notebook: a move or rename into a name that's taken stops before
anything changes, and only an empty folder can be deleted. Nothing in Fabric changes; a notebook in
a folder publishes as an item named after its last part (``leaf_name``), in no Fabric folder.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .notebook import (FOLDER_SEP, MAX_FOLDER_DEPTH, NotebookShelf, check_folder_part, check_notebook_name,
                       folder_of, is_notebook_folder, leaf_name, notebook_files)


def check_folder(path: str) -> str:
    """A folder path ("Sales", "Sales/Archive") with each part checked, at most MAX_FOLDER_DEPTH deep."""
    whole = str(path or "").strip().strip(FOLDER_SEP)
    if not whole:
        raise ValueError("Give the folder a name.")
    parts = whole.split(FOLDER_SEP)
    if len(parts) > MAX_FOLDER_DEPTH:
        raise ValueError(f"Folders go {MAX_FOLDER_DEPTH} levels deep at most.")
    return FOLDER_SEP.join(check_folder_part(p, whole) for p in parts)


def _where(place: dict[str, Any] | None) -> str:
    return f"the {place['label']} repository" if place else "the app's notebooks folder"


def _place(shelf: NotebookShelf, root: str | None) -> tuple[dict[str, Any] | None, Path]:
    return shelf._place(root)  # noqa: SLF001 - the shelf's own rule for places


def _dirs(base: Path) -> list[str]:
    """Every folder of notebooks under ``base``, as paths relative to it."""
    out: list[str] = []

    def walk(d: Path, prefix: str, depth: int) -> None:
        try:
            entries = sorted(p for p in d.iterdir() if p.is_dir() and is_notebook_folder(p.name))
        except OSError:
            return  # silent-ok: a folder that went away lists nothing
        for p in entries:
            out.append(prefix + p.name)
            if depth + 1 < MAX_FOLDER_DEPTH:
                walk(p, f"{prefix}{p.name}{FOLDER_SEP}", depth + 1)

    if base.is_dir():
        walk(base, "", 0)
    return out


def listing(shelf: NotebookShelf) -> list[dict[str, Any]]:
    """Every folder in every place, empty ones too: {"path", "name", "parent", "place", "notebooks"}
    where ``notebooks`` counts the notebooks in it and its subfolders."""
    rows = shelf.list()
    out = []
    for place, base in shelf._folders():  # noqa: SLF001
        where = {"root": place["root"], "label": place["label"]} if place else None
        names = [r["name"] for r in rows if (r.get("place") or None) == where]
        for path in _dirs(base):
            out.append({"path": path, "name": leaf_name(path), "parent": folder_of(path), "place": where,
                        "notebooks": sum(n.startswith(path + FOLDER_SEP) for n in names)})
    return out


def create(shelf: NotebookShelf, path: str, root: str | None) -> dict[str, Any]:
    """Makes the folder (and any folder above it) in the place. Refuses one that is already there."""
    path = check_folder(path)
    place, base = _place(shelf, root)
    target = base / path
    if target.exists():
        raise ValueError(f"A folder named {path!r} is already in {_where(place)}.")
    target.mkdir(parents=True)
    return {"path": path, "place": {"root": place["root"], "label": place["label"]} if place else None}


def _inside(shelf: NotebookShelf, base: Path, path: str) -> list[str]:
    """The names of the notebooks in the folder (and its subfolders) that open from this place."""
    names = [f"{path}{FOLDER_SEP}{n}" for n, _ in notebook_files(base / path)]
    return [n for n in names if shelf.path(n).parent.resolve().is_relative_to((base / path).resolve())]


def rename_plan(shelf: NotebookShelf, path: str, new_name: str, root: str | None) -> dict[str, Any]:
    """What renaming the folder's last part to ``new_name`` would do, or ValueError when it can't."""
    path = check_folder(path)
    new_name = check_folder_part(new_name)
    place, base = _place(shelf, root)
    src = base / path
    if not src.is_dir():
        raise ValueError(f"There's no folder {path!r} in {_where(place)}.")
    parent = folder_of(path)
    new_path = f"{parent}{FOLDER_SEP}{new_name}" if parent else new_name
    dest = base / new_path
    if new_path == path:
        return {"path": path, "new_path": path, "moves": [], "src": src, "dest": dest}
    if dest.exists() and not _same(src, dest):
        raise ValueError(f"A folder named {new_path!r} is already in {_where(place)}. Nothing was renamed.")
    moves = []
    for old in _inside(shelf, base, path):
        new = new_path + old[len(path):]
        check_notebook_name(new)
        if new.lower() != old.lower() and shelf._taken(new):  # noqa: SLF001 - names are unique across places
            raise ValueError(f"A notebook named {new!r} already exists. Nothing was renamed.")
        moves.append({"from": old, "to": new})
    return {"path": path, "new_path": new_path, "moves": moves, "src": src, "dest": dest}


def _same(a: Path, b: Path) -> bool:
    """True when two paths are one folder (a change of case on Windows or macOS)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def rename(shelf: NotebookShelf, plan: dict[str, Any]) -> None:
    """Renames the folder on disk, with every notebook and resources folder in it, in one step."""
    if plan["new_path"] == plan["path"]:
        return
    plan["src"].rename(plan["dest"])


def delete(shelf: NotebookShelf, path: str, root: str | None) -> dict[str, Any]:
    """Removes the folder only when nothing is in it. A folder with notebooks, other folders or any
    file in it is refused, saying what's there, so no notebook is ever deleted with a folder."""
    path = check_folder(path)
    place, base = _place(shelf, root)
    target = base / path
    if not target.is_dir():
        raise ValueError(f"There's no folder {path!r} in {_where(place)}.")
    entries = list(target.iterdir())
    if entries:
        books = len(notebook_files(target))
        what = (f"{books} notebook{'s' if books != 1 else ''}" if books else
                f"{len(entries)} other item{'s' if len(entries) != 1 else ''} (files or folders)")
        raise ValueError(f"{path} has {what} in it, so it wasn't deleted. Move or delete "
                         f"{'them' if books != 1 else 'it'} first: only an empty folder can be deleted.")
    target.rmdir()
    return {"deleted": path, "place": {"root": place["root"], "label": place["label"]} if place else None}


def move_target(shelf: NotebookShelf, name: str, folder: str) -> str:
    """The name the notebook gets in ``folder`` ("" for no folder), in the same place. Refuses a
    folder that isn't there and a name that is taken."""
    name = check_notebook_name(name)
    if not shelf.path(name).is_file():
        raise ValueError(f"There's no notebook named {name!r}.")
    folder = check_folder(folder) if folder else ""
    new = f"{folder}{FOLDER_SEP}{leaf_name(name)}" if folder else leaf_name(name)
    check_notebook_name(new)
    if new == name:
        return new
    place, base = shelf._where(name)  # noqa: SLF001
    if folder and not (base / folder).is_dir():
        raise ValueError(f"There's no folder {folder!r} in {_where(place)}. Make it with New folder first.")
    if shelf._taken(new):  # noqa: SLF001
        where = folder or "the top level"
        raise ValueError(f"{where} already has a notebook named {leaf_name(name)!r}. Nothing was moved.")
    return new

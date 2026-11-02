"""A notebook's resources folder: files kept beside the notebook, like Fabric's built-in resources.

Each notebook ``<name>.ipynb`` has ``<name>.resources/`` next to it, so the files go wherever the
notebook goes (a notebooks repository, Git). The files themselves are in its ``builtin/`` folder,
as in Fabric, where ``notebookutils.nbResPath`` is the folder that holds ``builtin``:

    Sales.ipynb
    Sales.resources/builtin/regions.csv

While a cell runs, ``builtin/...`` in its code means that folder (notebook.Kernel rewrites string
literals that start with it), so ``pd.read_csv("builtin/regions.csv")`` runs unchanged. The folder
is made when the first file is added; a notebook without files has none.

The resources stay on this PC: publishing the notebook to Fabric doesn't upload them.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
from pathlib import Path
from typing import Any
from . import jsonfile

MAX_FILES = 100
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_NAME = 120
SUFFIX = ".resources"
BUILTIN = "builtin"

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_PREFIX = re.compile(r"^(?:\./)?builtin[/\\]")


def check_name(name: str) -> str:
    """A file name that stays inside the folder and works on Windows, or ValueError saying why not."""
    name = str(name or "").strip()
    if not name or name in (".", ".."):
        raise ValueError("Give the file a name.")
    if _BAD.search(name):
        raise ValueError(f"{name!r} can't be a file name here: avoid / \\ : * ? \" < > | and control characters.")
    if name.endswith((".", " ")):
        raise ValueError(f"{name!r} can't end with a dot or a space on Windows.")
    if name.split(".")[0].upper().rstrip() in _RESERVED:
        raise ValueError(f"{name!r} is a name Windows keeps for devices. Pick another name.")
    if len(name) > MAX_NAME:
        raise ValueError(f"Keep file names to {MAX_NAME} characters.")
    return name


def folder_for(notebook_file: Path) -> Path:
    """``<name>.resources`` beside ``<name>.ipynb``."""
    return notebook_file.with_name(notebook_file.stem + SUFFIX)


def resolve(text: str, folder: str | Path) -> str | None:
    """``builtin/x.csv`` (or ``./builtin/x.csv``) as a path in ``folder``'s builtin folder; None
    for any other text, including the bare word ``builtin``. Forward slashes, which open(), pandas,
    polars and DuckDB all take. A ``..`` that would climb out of builtin is dropped, so the path
    always stays inside the notebook's folder."""
    m = _PREFIX.match(text)
    if not m:
        return None
    rest = text[m.end():]
    if ".." in re.split(r"[/\\]", rest):
        norm = posixpath.normpath(rest.replace("\\", "/"))
        while norm == ".." or norm.startswith("../"):
            norm = norm[3:]
        rest = "" if norm in ("", ".") else norm
    return f"{(Path(folder) / BUILTIN).as_posix()}/{rest}"


OUTSIDE = "That Built-in file links outside the notebook's resources."


def real_path(text: str, folder: str | Path) -> str | None:
    """Like ``resolve``, but the file's real path once every link is followed (symlinks, Windows
    junctions and other reparse points), for code that reads or runs the file itself (%run -b,
    %pip install -r). PermissionError when that path, or the builtin folder itself, is outside the
    notebook's resources folder, as kratosforge:// links refuse a path that leaves its folder."""
    path = resolve(text, folder)
    if path is None:
        return None
    resources = os.path.realpath(folder)
    root = os.path.realpath(Path(folder) / BUILTIN)
    real = os.path.realpath(path)
    if not (_within(root, resources) and _within(real, root)):
        raise PermissionError(OUTSIDE)
    return real


def _within(path: str, root: str) -> bool:
    try:
        return os.path.normcase(os.path.commonpath([path, root])) == os.path.normcase(root)
    except ValueError:  # another drive on Windows
        return False


def _free(base: Path, name: str) -> str:
    stem, dot, ext = name.rpartition(".") if "." in name.lstrip(".") else (name, "", "")
    for n in range(2, 1000):
        candidate = f"{stem} ({n}){dot}{ext}"
        if not (base / candidate).exists():
            return candidate
    raise ValueError("Couldn't find a free name for this file.")


class Resources:
    """The files of one notebook's resources folder (``folder`` is ``<name>.resources``)."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.files = folder / BUILTIN

    def _entries(self) -> list[os.DirEntry[str]]:
        if not self.files.is_dir():
            return []
        with os.scandir(self.files) as it:
            return [e for e in it if e.is_file()]

    def list(self) -> dict[str, Any]:
        rows = []
        for e in self._entries():
            st = e.stat()
            rows.append({"name": e.name, "size": st.st_size, "modified": st.st_mtime, "path": f"{BUILTIN}/{e.name}"})
        rows.sort(key=lambda r: r["name"].lower())
        return {"folder": str(self.folder), "files": rows, "max_files": MAX_FILES, "max_file_bytes": MAX_FILE_BYTES}

    def file(self, name: str) -> Path:
        path = self.files / check_name(name)
        if not path.is_file():
            raise FileNotFoundError(f"There's no resource file named {name!r}.")
        return path

    def add(self, name: str, data: bytes) -> dict[str, Any]:
        """Copy ``data`` in as ``name``. A file already there is never replaced: the answer has
        ``exists`` and a ``suggested`` free name instead, and nothing is written."""
        name = check_name(name)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"{name} is {len(data) / 1024 ** 2:.1f} MB. Resource files can be up to "
                             f"{MAX_FILE_BYTES // 1024 ** 2} MB each.")
        if len(self._entries()) >= MAX_FILES:
            raise ValueError(f"This notebook already has {MAX_FILES} resource files, the most it can have. "
                             "Delete one to add another.")
        self.files.mkdir(parents=True, exist_ok=True)
        path = self.files / name
        try:
            out = open(path, "xb")  # reserves the name: refuses when a file is already there
        except FileExistsError:
            return {"added": False, "exists": True, "name": name, "suggested": _free(self.files, name)}
        try:
            with out:
                out.write(data)
        except OSError:
            path.unlink(missing_ok=True)  # never leave a partial file that blocks a retry
            raise
        return {"added": True, "name": name, "size": len(data), "path": f"{BUILTIN}/{name}"}

    def rename(self, name: str, new_name: str) -> dict[str, Any]:
        src = self.file(name)
        new_name = check_name(new_name)
        if new_name == src.name:
            return {"name": new_name, "path": f"{BUILTIN}/{new_name}"}
        dest = self.files / new_name
        same_file = os.path.normcase(new_name) == os.path.normcase(src.name)  # a case change on Windows
        if not same_file:
            try:
                open(dest, "xb").close()  # reserved, so another file of that name is never replaced
            except FileExistsError:
                raise ValueError(f"A resource file named {new_name!r} already exists.") from None
        try:
            jsonfile.replace(src, dest)
        except OSError:
            if not same_file:
                dest.unlink(missing_ok=True)
            raise
        return {"name": new_name, "path": f"{BUILTIN}/{new_name}"}

    def delete(self, name: str) -> dict[str, Any]:
        self.file(name).unlink()
        return {"deleted": name}


def count(folder: Path) -> int:
    return len(Resources(folder)._entries())


def rename_folder(src: Path, dest: Path) -> bool:
    """Moves a notebook's resources folder with its renamed notebook. False when it has none.
    Refuses (before the notebook is renamed) when ``dest`` is already there."""
    if not src.is_dir():
        return False
    if dest.exists():
        raise ValueError(f"A folder named {dest.name!r} is already beside the notebook. Move or delete it, "
                         "then rename again. Nothing was renamed.")
    src.rename(dest)
    return True


def copy_folder(src: Path, dest: Path) -> int:
    """Copies the resources folder to ``dest`` (which must not exist) and checks every file landed
    at its size. Returns the number of files; on a failure the partial copy is removed."""
    if not src.is_dir():
        return 0
    if dest.exists():
        raise ValueError(f"A folder named {dest.name!r} is already there. Nothing was moved.")
    try:
        shutil.copytree(src, dest)
        files = {p.relative_to(src): p.stat().st_size for p in src.rglob("*") if p.is_file()}
        for rel, size in files.items():
            if not (dest / rel).is_file() or (dest / rel).stat().st_size != size:
                raise OSError(f"the copy of {rel.as_posix()} doesn't match")
    except OSError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return len(files)


def remove_folder(folder: Path) -> int:
    """Deletes the resources folder; returns how many files it held."""
    if not folder.is_dir():
        return 0
    n = sum(1 for p in folder.rglob("*") if p.is_file())
    shutil.rmtree(folder)
    return n

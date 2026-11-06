"""``notebookutils`` (and ``mssparkutils``) in an Academy PySpark exercise: the calls a Fabric notebook
uses to orchestrate other notebooks and handle files, bound to the exercise's folder.

It runs in the learner's process as part of the generated ``kf_learner`` package (learner_pkg.py), so
it holds no sign-in, lakehouse or network code, and everything it touches is inside the exercise:

    notebook.run("clean_orders", 90, {"run_date": "2026-11-01"})   runs notebooks/clean_orders.py
    notebook.exit("12 rows")                                      ends the notebook with that value
    notebook.runMultiple(dag)                                     runs a DAG of them, one at a time
    fs.ls("Files/landing")                                        the exercise's Files/ folder
    runtime.context["currentNotebookName"]

A notebook is a ``.py`` file in the exercise's ``notebooks/`` folder, in Fabric's ``.py`` format: cells
start at lines like ``# CELL ********************``, and the arguments ``run`` is given are set after
the cell that starts ``# PARAMETERS CELL ********************`` (before the first cell when there is
none), as Fabric injects them. It runs in this process with its own variables, ``spark`` shared.

What a Fabric notebook gets from its workspace (credentials, Key Vault, lakehouse and notebook items,
mounts, variable libraries) isn't in an exercise: using it raises an error that says so.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import types
from pathlib import Path
from typing import Any, Callable

from .. import notebook_dag

NOTEBOOKS = "notebooks"
FILES = "Files"
MAX_RUN_DEPTH = 10   # as notebook.py: a notebook that runs itself stops instead of looping
_NOTEBOOK = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")
_MARKER = re.compile(r"^# (?:[A-Z]+ )*\*{20}\s*$")
_PARAMETERS = "# PARAMETERS CELL"
_PREFIXES = ("file:/lakehouse/default/", "/lakehouse/default/", "lakehouse/default/")


class NotebookExit(Exception):
    """Raised by notebook.exit; ``run`` returns its value, and at the top it ends the answer quietly."""

    def __init__(self, value: Any):
        super().__init__(str(value))
        self.value = value


def cells(text: str) -> list[tuple[bool, int, str]]:
    """A notebook's cells as (is the parameters cell, first line number, source)."""
    out: list[tuple[bool, int, str]] = []
    params, start = False, 1
    lines: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        if _MARKER.match(line):
            if lines:
                out.append((params, start, "\n".join(lines)))
            params, start, lines = line.startswith(_PARAMETERS), n + 1, []
        else:
            lines.append(line)
    if lines:
        out.append((params, start, "\n".join(lines)))
    return out


def _no_link(path: Path, stop: Path) -> None:
    """Refuse ``path`` if it, or a folder on the way to it from ``stop``, is a link or junction."""
    for p in [path, *path.parents]:
        if p == stop:
            return
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            raise PermissionError(f"{p.name} is a link; an exercise's files and notebooks are plain files and folders.")
    raise PermissionError("That path isn't in this exercise.")


class _Absent:
    """A part of notebookutils that needs Fabric: any use says so."""

    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("__"):
            raise AttributeError(attr)
        raise NotImplementedError(f"notebookutils.{self._name}.{attr} isn't in an Academy exercise: the exercise has no "
                                  "sign-in, Key Vault, lakehouse items or mounts. It works in a Fabric notebook.")


class FileInfo:
    """What fs.ls returns for each entry, with Fabric's names."""

    def __init__(self, path: str, local: Path):
        st = local.stat()
        self.name = local.name
        self.path = path
        self.isDir = local.is_dir()  # noqa: N815 - Fabric's name
        self.size = 0 if self.isDir else st.st_size
        self.modifyTime = int(st.st_mtime * 1000)  # noqa: N815

    def isFile(self) -> bool:  # noqa: N802
        return not self.isDir

    def __repr__(self) -> str:
        return f"FileInfo(path={self.path}, name={self.name}, size={self.size})"


class Utils:
    """The ``notebookutils`` an exercise's code sees."""

    def __init__(self, work: Path, namespace: Callable[[], dict[str, Any]]):
        self.work = Path(work)
        self._namespace = namespace
        self._running: list[str] = []
        self.notebook = types.SimpleNamespace(run=self.run, exit=self.exit, runMultiple=self.run_multiple,
                                              validateDAG=self.validate_dag, help=self._help_notebook)
        self.fs = types.SimpleNamespace(ls=self.ls, exists=self.exists, head=self.head, put=self.put,
                                        append=self.append, mkdirs=self.mkdirs, rm=self.rm, cp=self.cp, mv=self.mv,
                                        help=self._help_fs)
        self.runtime = types.SimpleNamespace(context=_Context(self))
        for name in ("credentials", "lakehouse", "variableLibrary", "udf", "warehouse", "connections", "session",
                     "env"):
            setattr(self, name, _Absent(name))

    # ---- notebooks

    def _notebook(self, name: str) -> Path:
        name = str(name)
        name = name[:-3] if name.endswith(".py") else name
        if not _NOTEBOOK.match(name):
            raise ValueError(f"{name!r} isn't a notebook name like clean_orders.")
        path = self.work / NOTEBOOKS / f"{name}.py"
        if not path.is_file():
            raise FileNotFoundError(f"There's no notebook {name} in this exercise (notebooks/{name}.py).")
        _no_link(path, self.work)
        return path

    def run(self, path: str, timeout_seconds: float = 90, arguments: dict[str, Any] | None = None,
            workspace: str = "", **_kw: Any) -> str | None:
        """Run another notebook of the exercise with ``arguments`` and return its exit value as a string
        (None if it didn't call exit). The exercise's own time limit is the limit here."""
        if workspace:
            raise NotImplementedError("An exercise's notebooks are all in the exercise: leave out workspace.")
        file = self._notebook(path)
        if len(self._running) >= MAX_RUN_DEPTH:
            raise RecursionError(f"Notebook {path!r} would be the {len(self._running) + 1}th notebook run inside "
                                 f"another; the exercise stops at {MAX_RUN_DEPTH}, so a notebook can't run itself forever.")
        args = dict(arguments or {})
        bad = [k for k in args if not (isinstance(k, str) and k.isidentifier())]
        if bad:
            raise ValueError(f"Notebook arguments are named like Python variables; {bad[0]!r} isn't.")
        ns = self._namespace()
        parts = cells(file.read_text(encoding="utf-8-sig"))
        if not any(p for p, _s, _c in parts):
            ns.update(args)
        self._running.append(file.stem)
        self.runtime.context._refresh()   # json.dumps reads the dict's own storage, not __getitem__
        try:
            for is_params, start, source in parts:
                exec(compile("\n" * (start - 1) + source, f"{NOTEBOOKS}/{file.name}", "exec"), ns)  # noqa: S102
                if is_params:
                    ns.update(args)
        except NotebookExit as done:
            return None if done.value is None else str(done.value)
        finally:
            self._running.pop()
            self.runtime.context._refresh()
        return None

    @staticmethod
    def exit(value: Any = "") -> None:  # noqa: A003 - Fabric's name
        raise NotebookExit(value)

    def run_multiple(self, dag: Any, timeout_seconds: int | None = None, **_kw: Any) -> dict[str, Any]:
        """Fabric's runMultiple, one notebook at a time (notebook_dag)."""
        return notebook_dag.run_multiple(dag, lambda path, _left, args, _cell: self.run(path, 0, args), timeout_seconds)

    @staticmethod
    def validate_dag(dag: Any) -> bool:
        notebook_dag.activities(dag)
        return True

    # ---- files

    def _local(self, path: str) -> Path:
        text = str(path).replace("\\", "/")
        for prefix in _PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):]
        parts = [p for p in text.split("/") if p not in ("", ".")]
        if text.startswith(("/", "abfss:", "file:", "http:", "https:")) or not parts or parts[0] != FILES or ".." in parts:
            raise ValueError(f"{path!r}: an exercise's files are under Files/, like Files/landing/orders.csv.")
        local = self.work.joinpath(*parts)
        _no_link(local, self.work)
        return local

    def ls(self, path: str) -> list[FileInfo]:
        local = self._local(path)
        if not local.exists():
            raise FileNotFoundError(f"{path} doesn't exist.")
        base = str(path).rstrip("/")
        entries = [local] if local.is_file() else sorted(local.iterdir())
        return [FileInfo(base if e == local else f"{base}/{e.name}", e) for e in entries]

    def exists(self, path: str) -> bool:
        return self._local(path).exists()

    def head(self, path: str, maxBytes: int = 65536) -> str:  # noqa: N803
        with open(self._local(path), "rb") as f:
            return f.read(int(maxBytes)).decode("utf-8", errors="replace")

    def put(self, path: str, content: str, overwrite: bool = False) -> bool:
        local = self._local(path)
        if local.exists() and not overwrite:
            raise FileExistsError(f"{path} exists; pass overwrite=True to replace it.")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(str(content), encoding="utf-8")
        return True

    def append(self, path: str, content: str, createFileIfNotExists: bool = False) -> bool:  # noqa: N803
        local = self._local(path)
        if not local.exists() and not createFileIfNotExists:
            raise FileNotFoundError(f"{path} doesn't exist; pass createFileIfNotExists=True to make it.")
        local.parent.mkdir(parents=True, exist_ok=True)
        with open(local, "a", encoding="utf-8") as f:
            f.write(str(content))
        return True

    def mkdirs(self, path: str) -> bool:
        self._local(path).mkdir(parents=True, exist_ok=True)
        return True

    def rm(self, path: str, recurse: bool = False) -> bool:
        local = self._local(path)
        if local == self.work / FILES:
            raise PermissionError("Files/ itself can't be removed.")
        if local.is_dir():
            if not recurse:
                raise IsADirectoryError(f"{path} is a folder; pass recurse=True to remove it.")
            shutil.rmtree(local)
        elif local.exists():
            local.unlink()
        else:
            raise FileNotFoundError(f"{path} doesn't exist.")
        return True

    def cp(self, src: str, dest: str, recurse: bool = False) -> bool:
        a, b = self._local(src), self._local(dest)
        if a.is_dir():
            if not recurse:
                raise IsADirectoryError(f"{src} is a folder; pass recurse=True to copy it.")
            shutil.copytree(a, b, dirs_exist_ok=True, symlinks=True)
        else:
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(a, b)
        return True

    def mv(self, src: str, dest: str, create_path: bool = False, overwrite: bool = False) -> bool:
        a, b = self._local(src), self._local(dest)
        if b.exists() and not overwrite:
            raise FileExistsError(f"{dest} exists; pass overwrite=True to replace it.")
        if create_path:
            b.parent.mkdir(parents=True, exist_ok=True)
        os.replace(a, b)
        return True

    @staticmethod
    def _help_notebook(*_a: Any) -> None:
        print("notebookutils.notebook in an exercise: run, exit, runMultiple, validateDAG (notebooks/ of the exercise)")

    @staticmethod
    def _help_fs(*_a: Any) -> None:
        print("notebookutils.fs in an exercise: ls, exists, head, put, append, mkdirs, rm, cp, mv (under Files/)")


class _Context(dict):
    """``runtime.context``: what a Fabric notebook reads about itself, for this exercise. A dict (so
    json.dumps prints it, as learners do), refreshed on every read, since the running notebook changes."""

    def __init__(self, utils: Utils):
        super().__init__()
        self._utils = utils
        self._refresh()

    def _refresh(self) -> None:
        running = self._utils._running
        super().clear()
        super().update({"currentNotebookName": running[-1] if running else "answer", "isLocal": True,
                        "isReferenceRun": bool(running), "isForPipeline": False,
                        "currentWorkspaceName": "Academy exercise", "defaultLakehouseName": "exercise"})

    def __getitem__(self, key: str) -> Any:
        self._refresh()
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        self._refresh()
        return super().get(key, default)

    def keys(self) -> Any:
        self._refresh()
        return super().keys()

    def items(self) -> Any:
        self._refresh()
        return super().items()

    def values(self) -> Any:
        self._refresh()
        return super().values()

    def __iter__(self) -> Any:
        self._refresh()
        return super().__iter__()

    def __contains__(self, key: object) -> bool:
        self._refresh()
        return super().__contains__(key)

    def __repr__(self) -> str:
        self._refresh()
        return super().__repr__()


def install(work: Path, namespace: Callable[[], dict[str, Any]]) -> types.ModuleType:
    """``notebookutils`` for this process: ``import notebookutils``, ``from notebookutils import
    mssparkutils`` and ``import mssparkutils`` all give it. ``namespace()`` is a fresh set of variables
    for a notebook ``run`` starts."""
    utils = Utils(work, namespace)
    module = types.ModuleType("notebookutils", "notebookutils for an Academy exercise (academy/notebookutils_lab.py)")
    for name in ("notebook", "fs", "runtime", "credentials", "lakehouse", "variableLibrary", "udf", "warehouse",
                 "connections", "session", "env"):
        setattr(module, name, getattr(utils, name))
    module.mssparkutils = module  # type: ignore[attr-defined]
    module.NotebookExit = NotebookExit  # type: ignore[attr-defined]
    sys.modules.update({"notebookutils": module, "mssparkutils": module})
    return module

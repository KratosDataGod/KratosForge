"""A file written past Windows' 260 characters, on a PC where long paths aren't turned on, is refused by
name, and the same writers work under a non-ASCII profile.

Without LongPathsEnabled, Windows answers a path past 260 characters with "The system cannot find the
path specified" (WinError 3) or "The filename or extension is too long" (206), which sends the person
looking for a folder that is there. Every swap of a saved file (jsonfile.write_text and write_bytes,
about 75 stores and settings), the notebook save, the SQL page's export, a result download, a notebook
or SQL template exported to Downloads, the support bundle, the backup, the SQLite stores (whose own
answer is "unable to open database file"), a new, deleted, or restored notebook, a resource file added
or renamed, and a notebook kept beside its versions now say instead which file, how long its path is,
and that long paths are off.

Off Windows the file system takes long paths, so Windows' answer is played by a stand-in for the calls
that open or make a file or folder (os.open, open, io.open, os.mkdir), which refuses any path of 260
characters or more as Windows does. Each writer is also run for real under C:\\Users\\Jöhn O'Brien 日本
(as tmp_path's subfolder), where it must just work.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import sqlite3
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from fabric_desktop import (backup, frames, jsonfile, known_folders, notebook, notebook_files, notebook_resources,
                            notebook_versions, sql_exec, sqlitefile, support_bundle, win_paths)
from fabric_desktop.integrations.store import IntegrationStore
from fabric_desktop.notebook import NotebookShelf
from fabric_desktop.notebook_formats_service import NotebookFormatsMixin
from fabric_desktop.sql_templates_service import FOLDER as TEMPLATES, SqlTemplatesMixin
from fabric_desktop.workloads.store import ManagementStore

PROFILE = ("Users", "Jöhn O'Brien 日本", "OneDrive - Contoso Ünited")
LONG_MESSAGE = ("couldn't be {doing}: its path is {n} characters, past the 260 Windows allows on this PC because long "
                "paths aren't turned on. Move it to a folder with a shorter path, or ask IT to turn on long paths.")


def _windows_refuses_long_paths(monkeypatch):
    """Play Windows without long paths: any file or folder opened or made at 260 characters or more is
    refused with WinError 3, as CreateFileW and CreateDirectoryW refuse it."""
    def refusing(real):
        def call(file, *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and len(os.path.abspath(os.fspath(file))) >= 260:
                exc = FileNotFoundError(2, "The system cannot find the path specified", os.fspath(file))
                exc.winerror = 3  # type: ignore[attr-defined]
                raise exc
            return real(file, *args, **kwargs)
        return call
    monkeypatch.setattr(os, "open", refusing(os.open))
    monkeypatch.setattr(os, "mkdir", refusing(os.mkdir))
    monkeypatch.setattr(builtins, "open", refusing(builtins.open))
    monkeypatch.setattr(io, "open", refusing(io.open))
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: False)


def _snapshot(folder: Path) -> Path:
    table = pa.table({"id": [1, 2], "name": ["Jöhn", "日本"]})
    snapshot = folder / "snapshot.arrow"
    with ipc.new_file(str(snapshot), table.schema) as writer:
        writer.write_table(table)
    return snapshot


def _sql_export(folder: Path, short: Path) -> tuple[str, Path]:
    out = folder / "result ö.csv"
    out.touch() if len(str(out)) < 260 else None  # the name the export reserved, where Windows would let it
    job = sql_exec.Export(id="e1", execution="r1", format="csv", path=out, total=2, raw=True)
    object.__new__(sql_exec.SqlExecutions)._write(_snapshot(short), job)
    if job.state != "completed":
        raise OSError(job.error)
    return "exported", out


def _download(folder: Path, short: Path) -> tuple[str, Path]:
    out = folder / "result ö.parquet"
    f = object.__new__(frames.Frames)
    f.table = lambda fid: pa.table({"id": [1, 2]})  # type: ignore[method-assign]
    f.download("f1", "parquet", str(out))
    return "downloaded", out


def _notebook_export(folder: Path, short: Path, monkeypatch) -> tuple[str, Path]:
    monkeypatch.setattr(known_folders, "downloads", lambda: folder)
    out = NotebookFormatsMixin.notebook_export(None, "Daily sales ö", [{"type": "code", "source": "x = 1"}],  # type: ignore[arg-type]
                                               "fabric_py")
    return "exported", Path(out["path"])


CELLS = [{"type": "code", "source": "x = 1"}]


def _template_export(folder: Path, short: Path, monkeypatch) -> tuple[str, Path]:
    (short / TEMPLATES).mkdir(exist_ok=True)
    (short / TEMPLATES / "Load ö.sql").write_text("select 1", encoding="utf-8")
    svc = object.__new__(SqlTemplatesMixin)
    svc.root = short  # type: ignore[attr-defined]
    svc.git_repos = lambda: {"repos": []}  # type: ignore[attr-defined]
    monkeypatch.setattr(known_folders, "downloads", lambda: folder)
    return "exported", Path(svc.sql_template_export("mine", "Load ö.sql")["path"])


def _notebook_create(folder: Path, short: Path) -> tuple[str, Path]:
    shelf = NotebookShelf(folder / "notebooks")
    notebook_files.create(shelf, "Daily ö", None, CELLS, None, None, None)
    return "saved", shelf.path("Daily ö")


def _notebook_delete(folder: Path, short: Path) -> tuple[str, Path]:
    """The notebook is in a repository with a short path; Recently deleted, beside the app's own
    notebooks folder, is past 260."""
    repo = short / "repo"
    shelf = NotebookShelf(folder / "notebooks", places=lambda: [(str(repo), "Repo", repo / "notebooks")])
    notebook_files.create(shelf, "Daily ö", str(repo), CELLS, None, None, None)
    tid = notebook_files.delete(shelf, "Daily ö", [])["id"]
    return "deleted", folder / "notebooks-deleted" / f"{tid}.ipynb"


def _notebook_restore(folder: Path, short: Path) -> tuple[str, Path]:
    """Deleted from the app's own folder (short), restored to the repository it came from (long)."""
    repo = folder / "repo"
    shelf = NotebookShelf(short / "notebooks", places=lambda: [(str(repo), "Repo", repo / "notebooks")])
    notebook_files.create(shelf, "Daily ö", None, CELLS, None, None, None)
    tid = notebook_files.delete(shelf, "Daily ö", [])["id"]
    meta = short / "notebooks-deleted" / f"{tid}.json"
    meta.write_text(json.dumps(json.loads(meta.read_text("utf-8")) | {"place": {"root": str(repo), "label": "Repo"}}),
                    encoding="utf-8")
    return "restored", Path(notebook_files.restore(shelf, tid) and repo / "notebooks" / "Daily ö.ipynb")


def _keep_copy(folder: Path, short: Path) -> tuple[str, Path]:
    src = short / "Daily ö.ipynb"
    src.write_bytes(b"<<<<<<< conflict")
    return "kept", notebook_versions.keep_copy(folder / "versions", "Daily ö", src)


WRITERS = {
    "settings and stores (jsonfile.write_text)":
        lambda folder, short, mp: (jsonfile.write_text(folder / "settings ö.json", "{}"), ("saved", folder / "settings ö.json"))[1],
    "pictures and SQL files (jsonfile.write_bytes)":
        lambda folder, short, mp: (jsonfile.write_bytes(folder / "query ö.sql", b"select 1"), ("saved", folder / "query ö.sql"))[1],
    "a notebook save": lambda folder, short, mp: (notebook._write_atomic(folder / "Daily ö.ipynb", "{}"),
                                                  ("saved", folder / "Daily ö.ipynb"))[1],
    "a SQL result export": lambda folder, short, mp: _sql_export(folder, short),
    "a result download": lambda folder, short, mp: _download(folder, short),
    "a notebook exported to Downloads": lambda folder, short, mp: _notebook_export(folder, short, mp),
    "a support bundle": lambda folder, short, mp: ("written", support_bundle.create(folder, data_dir=short)),
    "a backup": lambda folder, short, mp: ("written", Path(backup.create(folder, data_dir=short)["path"])),
    "a SQL template exported to Downloads": _template_export,
    "a SQLite store that refuses a damaged file (workloads)":
        lambda folder, short, mp: (ManagementStore(folder / "stores" / "workloads ö.db"),
                                   ("opened", folder / "stores" / "workloads ö.db"))[1],
    "a SQLite store that sets a damaged file aside (integrations)":
        lambda folder, short, mp: (IntegrationStore(folder / "stores" / "integrations ö.db"),
                                   ("opened", folder / "stores" / "integrations ö.db"))[1],
    "a new notebook": lambda folder, short, mp: _notebook_create(folder, short),
    "a notebook moved to Recently deleted": lambda folder, short, mp: _notebook_delete(folder, short),
    "a notebook restored from Recently deleted": lambda folder, short, mp: _notebook_restore(folder, short),
    "a resource file added": lambda folder, short, mp: (
        notebook_resources.Resources(folder / "Daily ö.resources").add("data ö.csv", b"a,b"),
        ("added", folder / "Daily ö.resources" / notebook_resources.BUILTIN / "data ö.csv"))[1],
    "a notebook kept beside its versions": lambda folder, short, mp: _keep_copy(folder, short),
}


@pytest.fixture
def short(tmp_path) -> Path:
    folder = tmp_path.joinpath(*PROFILE)
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def long(short) -> Path:
    folder = short.joinpath(*(["a-folder-name-of-forty-characters-long-x"] * 6))
    folder.mkdir(parents=True)
    assert len(str(folder)) > 260
    return folder


@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_each_writer_works_under_a_non_ascii_profile(writer, short, monkeypatch):
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: False)  # short paths need no setting
    _doing, path = WRITERS[writer](short, short, monkeypatch)
    assert path.is_file() and path.stat().st_size > 0
    assert str(short) in str(path)


@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_each_writer_past_260_characters_without_long_paths_names_the_file_and_the_setting(writer, short, long,
                                                                                           monkeypatch):
    _windows_refuses_long_paths(monkeypatch)
    with pytest.raises(OSError) as caught:
        WRITERS[writer](long, short, monkeypatch)
    text = str(caught.value)
    assert "couldn't be " in text and "past the 260 Windows allows on this PC because long paths aren't turned on" in text, text
    assert "WinError" not in text and "cannot find the path" not in text, text
    assert str(long) not in text  # the file's name, not its folder (which holds the user name)
    leftovers = [p.name for p in long.iterdir()]
    assert leftovers == [], f"a refused write left {leftovers}"


def test_the_message_counts_the_longer_of_the_file_and_what_windows_was_asked_for(tmp_path):
    target = "C:\\" + "x" * 240 + "\\a.json"  # 249: under the limit, but its temporary file beside it isn't
    exc = FileNotFoundError(2, "The system cannot find the path specified", target[:-6] + ".a.json.k2j9x8.tmp" + "y" * 10)
    exc.winerror = 3  # type: ignore[attr-defined]
    n = len(os.path.abspath(exc.filename))
    assert win_paths.too_long(exc, target, "saved", False) == "a.json " + LONG_MESSAGE.format(doing="saved", n=n)


@pytest.mark.parametrize(("winerror", "long_paths", "length", "named"), [
    (3, False, 300, True), (206, False, 300, True),
    (3, True, 300, False),   # long paths on: Windows' own answer is the right one
    (3, None, 300, False),   # off Windows, or not known
    (3, False, 100, False),  # a short path that really isn't there
    (5, False, 300, False),  # access denied: file_locks says why
])
def test_only_a_path_windows_refuses_for_its_length_is_named(winerror, long_paths, length, named):
    path = "C:\\" + "d" * (length - 10) + "\\x.csv"
    exc = FileNotFoundError(2, "The system cannot find the path specified", path)
    exc.winerror = winerror  # type: ignore[attr-defined]
    assert (win_paths.too_long(exc, path, "exported", long_paths) is not None) is named


def test_the_error_raised_keeps_what_code_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: False)
    path = tmp_path / ("d" * 300) / "x.json"
    exc = FileNotFoundError(2, "The system cannot find the path specified", str(path))
    exc.winerror = 3  # type: ignore[attr-defined]
    err = jsonfile.path_too_long(exc, path)
    assert isinstance(err, FileNotFoundError) and err.errno == 2 and err.winerror == 3  # type: ignore[attr-defined]
    assert err.path == str(path)  # type: ignore[attr-defined]
    assert jsonfile.path_too_long(ValueError("x"), path) is None


def test_a_resource_file_renamed_past_260_characters_names_the_new_name(short, long, monkeypatch):
    files = notebook_resources.Resources(long / "Daily ö.resources")
    files.add("data.csv", b"a,b")  # before Windows is played: the file is already there
    _windows_refuses_long_paths(monkeypatch)
    with pytest.raises(OSError) as caught:
        files.rename("data.csv", "data ö 2026.csv")
    text = str(caught.value)
    assert text.startswith("data ö 2026.csv couldn't be renamed: its path is "), text
    assert "WinError" not in text and str(long) not in text, text
    assert sorted(p.name for p in files.files.iterdir()) == ["data.csv"]  # nothing renamed, nothing reserved


def _sqlite_refuses_long_paths(monkeypatch):
    """SQLite on Windows without long paths: the folder is there, but the file can't be opened, and all
    SQLite says is "unable to open database file"."""
    real = sqlite3.connect

    def connect(database, *args, **kwargs):
        if len(os.path.abspath(os.fspath(database))) >= 260:
            raise sqlite3.OperationalError("unable to open database file")
        return real(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)


@pytest.mark.parametrize("store", [ManagementStore, IntegrationStore])
def test_a_sqlite_store_past_260_characters_says_so_by_name_and_keeps_sqlites_answer(store, long, monkeypatch):
    _sqlite_refuses_long_paths(monkeypatch)
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: False)
    with pytest.raises(OSError) as caught:
        store(long / "store ö.db")
    text = str(caught.value)
    assert text.startswith("store ö.db couldn't be opened: its path is "), text
    assert "past the 260 Windows allows on this PC because long paths aren't turned on" in text, text
    assert str(long) not in text
    assert isinstance(caught.value.__cause__, sqlite3.OperationalError)


@pytest.mark.parametrize("long_paths", [True, None])
def test_sqlites_own_answer_stands_when_long_paths_are_on_or_not_known(long_paths, long, monkeypatch):
    _sqlite_refuses_long_paths(monkeypatch)
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: long_paths)
    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        ManagementStore(long / "store.db")


def test_sqlites_own_answer_stands_for_a_short_path_or_another_failure(short, monkeypatch):
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: False)
    for error in ("unable to open database file", "database is locked"):
        def first_open(error=error):
            raise sqlite3.OperationalError(error)
        with pytest.raises(sqlite3.OperationalError, match=error):
            sqlitefile.open_store(short / "store.db", first_open, refuse="{path}: {reason}")

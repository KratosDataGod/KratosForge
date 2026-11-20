"""A file written past Windows' 260 characters, on a PC where long paths aren't turned on, is refused by
name, and the same writers work under a non-ASCII profile.

Without LongPathsEnabled, Windows answers a path past 260 characters with "The system cannot find the
path specified" (WinError 3) or "The filename or extension is too long" (206), which sends the person
looking for a folder that is there. Every swap of a saved file (jsonfile.write_text and write_bytes,
about 75 stores and settings), the notebook save, the SQL page's export, a result download, a notebook
or SQL template exported to Downloads, the support bundle and the backup now say instead which file,
how long its path is, and that long paths are off.

Off Windows the file system takes long paths, so Windows' answer is played by a stand-in for the calls
that open or make a file or folder (os.open, open, io.open, os.mkdir), which refuses any path of 260
characters or more as Windows does. Each writer is also run for real under C:\\Users\\Jöhn O'Brien 日本
(as tmp_path's subfolder), where it must just work.
"""

from __future__ import annotations

import builtins
import io
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from fabric_desktop import backup, frames, jsonfile, known_folders, notebook, sql_exec, support_bundle, win_paths
from fabric_desktop.notebook_formats_service import NotebookFormatsMixin

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

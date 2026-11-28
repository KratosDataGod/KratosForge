"""A local data source folder whose files or folders are past Windows' 260 characters, on a PC where long
paths are off, under a non-ASCII profile.

Reading such a file failed however the reader put it (DuckDB: 'No files found that match the pattern',
Windows: 'The system cannot find the path specified'), which sends the person looking for a file that is
there. The table's error and the folder's issue now say which file or folder, how long its path is, and
that long paths are off. Off Windows the file system takes long paths, so Windows' answer is played by
stand-ins for os.scandir and DuckDB's read; a Windows PC with long paths off is UNVERIFIED by this test.
"""

from __future__ import annotations

import os

import duckdb
import pytest

from fabric_desktop import support_bundle
from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import discovery
from fabric_desktop.localdata import engine as engine_mod

PROFILE = ("Users", "Jöhn O'Brien 日本", "OneDrive - Contoso Ünited")


def _too_long(path: str) -> bool:
    return len(os.path.abspath(path)) >= 260


@pytest.fixture
def windows_without_long_paths(monkeypatch):
    real_scandir, real_select = os.scandir, engine_mod.file_select

    def scandir(path):
        if _too_long(os.fspath(path)):
            exc = FileNotFoundError(2, "The system cannot find the path specified", os.fspath(path))
            exc.winerror = 3  # type: ignore[attr-defined]
            raise exc
        return real_scandir(path)

    def file_select(found):
        for p in found.paths:
            if _too_long(p):
                raise duckdb.IOException(f'IO Error: No files found that match the pattern "{p}"')
        return real_select(found)

    monkeypatch.setattr(discovery.os, "scandir", scandir)
    monkeypatch.setattr(engine_mod, "file_select", file_select)
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: False)


def _source(tmp_path):
    root = tmp_path.joinpath(*PROFILE, "Sales data ö")
    root = root / ("d" * max(1, 200 - len(str(root))))
    root.mkdir(parents=True)
    (root / "short ö.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    long_file = root / ("long_" + "n" * (262 - len(str(root)) - 10) + ".csv")
    long_file.write_text("a,b\n1,2\n", encoding="utf-8")
    deep = root / ("deep" + "x" * (265 - len(str(root))))
    deep.mkdir()
    (deep / "inside.csv").write_text("a\n1\n", encoding="utf-8")
    return root, long_file, deep


def test_a_file_or_folder_past_260_characters_is_named_with_long_paths_off(tmp_path, windows_without_long_paths):
    root, long_file, deep = _source(tmp_path)
    data = LocalData(tmp_path / "local-data")
    try:
        pid = data.create("long sources")["id"]
        added = data.add_source(pid, str(root))
        tables = {t["table"]: t for t in added["tables"]}
        assert tables["short ö"]["status"] == "ok"
        n = len(str(long_file))
        assert tables[long_file.stem]["error"] == (
            f"{long_file.name} couldn't be read, because its path is {n} characters, past the 260 Windows allows on "
            "this PC while long paths are turned off. Move it to a folder with a shorter path, or ask IT to turn on "
            "long paths.")
        assert {"path": str(deep), "problem": (
            f"{deep.name} couldn't be opened, because its path is {len(str(deep))} characters, past the 260 Windows "
            "allows on this PC while long paths are turned off. Move it to a folder with a shorter path, or ask IT to "
            "turn on long paths.")} in added["issues"]
    finally:
        data.close_all()


def test_with_long_paths_on_or_unknown_the_readers_own_words_stay(tmp_path, windows_without_long_paths, monkeypatch):
    root, long_file, deep = _source(tmp_path)
    data = LocalData(tmp_path / "local-data")
    try:
        pid = data.create("long sources")["id"]
        monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: None)
        added = data.add_source(pid, str(root))
        tables = {t["table"]: t for t in added["tables"]}
        assert tables[long_file.stem]["error"].startswith("IO Error: No files found")
        assert {"path": str(deep), "problem": "Can't open the folder: The system cannot find the path specified"} \
            in added["issues"]
    finally:
        data.close_all()

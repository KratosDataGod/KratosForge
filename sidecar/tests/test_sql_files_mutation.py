"""sql_files.py paths that mutation testing (batch 76, docs/quality/mutation-testing.md) showed no test
pinned: the 5 MB limit and its exact edge on read and save, the size named in the refusal, a folder named
.sql, each refused save argument, and how a reparse point is told apart from a plain file."""

from __future__ import annotations

import stat
from types import SimpleNamespace

import pytest

from fabric_desktop import sql_files

MB5 = 5 * 1024 * 1024


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_the_limit_is_5_mb():
    assert sql_files.MAX_BYTES == MB5 == 5242880


def test_a_file_of_exactly_5_mb_opens_and_one_byte_more_is_refused_as_5_mb(tmp_path):
    f = tmp_path / "q.sql"
    f.write_bytes(b"x" * MB5)
    assert sql_files.read(str(f))["bytes"] == MB5
    f.write_bytes(b"x" * (MB5 + 1))
    with pytest.raises(ValueError, match="larger than 5 MB"):
        sql_files.read(str(f))


def test_a_save_of_exactly_5_mb_is_written_and_one_byte_more_is_refused_as_5_mb(tmp_path):
    f = tmp_path / "q.sql"
    assert sql_files.write(str(f), "x" * MB5, newline="\n")["bytes"] == MB5
    with pytest.raises(ValueError, match="larger than 5 MB"):
        sql_files.write(str(tmp_path / "r.sql"), "x" * (MB5 + 1), newline="\n")
    assert not (tmp_path / "r.sql").exists()


def test_a_folder_named_sql_isnt_a_file(tmp_path):
    (tmp_path / "dir.sql").mkdir()
    with pytest.raises(ValueError, match="isn't a file"):
        sql_files.read(str(tmp_path / "dir.sql"))


@pytest.mark.parametrize("kw, match", [
    ({"text": None}, "text must be the query text"),
    ({"encoding": "latin-1"}, "encoding must be one of utf-8, utf-8-sig, utf-16"),
    ({"newline": "\r"}, "newline must be CRLF or LF"),
])
def test_a_save_with_a_bad_argument_is_refused_and_writes_nothing(tmp_path, kw, match):
    args = {"text": "SELECT 1", **kw}
    with pytest.raises(ValueError, match=match):
        sql_files.write(str(tmp_path / "q.sql"), **args)
    assert not (tmp_path / "q.sql").exists()


def fake_lstat(monkeypatch, **fields):
    st = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, **fields)
    monkeypatch.setattr(sql_files.os, "lstat", lambda _p: st)


def test_a_windows_reparse_point_is_refused_as_a_link(tmp_path, monkeypatch):
    fake_lstat(monkeypatch, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
    with pytest.raises(ValueError, match="is a link"):
        sql_files.read(str(tmp_path / "j.sql"))


def test_a_stat_without_file_attributes_is_never_a_reparse_point(tmp_path, monkeypatch):
    fake_lstat(monkeypatch)  # no st_file_attributes, as off Windows
    monkeypatch.setattr(sql_files.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1)
    assert sql_files._linked(tmp_path / "q.sql") is False


def test_a_python_without_the_reparse_constant_reads_no_attribute_as_one(tmp_path, monkeypatch):
    fake_lstat(monkeypatch, st_file_attributes=1)
    monkeypatch.delattr(sql_files.stat, "FILE_ATTRIBUTE_REPARSE_POINT", raising=False)
    assert sql_files._linked(tmp_path / "q.sql") is False

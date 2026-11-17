"""Editor parity S38: .sql files opened and saved from the SQL page (sql_files.py). Local files only."""

from __future__ import annotations

import codecs
import os

import pytest

from fabric_desktop import audit, server, sql_files
from fabric_desktop.service import Service


def test_a_file_reads_with_its_encoding_line_endings_and_version(tmp_path):
    f = tmp_path / "q.sql"
    f.write_bytes(codecs.BOM_UTF8 + "SELECT 'é'\r\nFROM t\r\n".encode())
    got = sql_files.read(str(f))
    assert got["text"] == "SELECT 'é'\nFROM t\n" and got["encoding"] == "utf-8-sig" and got["newline"] == "\r\n"
    assert got["version"] == sql_files.info(str(f))["version"] and got["name"] == "q.sql"


def test_a_save_keeps_the_files_encoding_and_line_endings(tmp_path):
    f = tmp_path / "q.sql"
    f.write_bytes("SELECT 1\r\n".encode("utf-16"))
    got = sql_files.read(str(f))
    assert got["encoding"] == "utf-16" and got["text"] == "SELECT 1\n"
    saved = sql_files.write(str(f), "SELECT 2\nFROM t\n", got["version"], got["encoding"], got["newline"])
    assert f.read_bytes() == "SELECT 2\r\nFROM t\r\n".encode("utf-16")
    assert saved["version"] != got["version"]
    lf = tmp_path / "lf.sql"
    lf.write_bytes(b"SELECT 1\n")
    r = sql_files.read(str(lf))
    sql_files.write(str(lf), "SELECT 3\nSELECT 4", r["version"], r["encoding"], r["newline"])
    assert lf.read_bytes() == b"SELECT 3\nSELECT 4"


def test_a_file_changed_on_disk_is_not_written_over(tmp_path):
    f = tmp_path / "q.sql"
    f.write_text("SELECT 1")
    seen = sql_files.read(str(f))["version"]
    f.write_text("SELECT 'someone else'")
    os.utime(f, ns=(1, 1))
    with pytest.raises(sql_files.Conflict, match="changed on disk"):
        sql_files.write(str(f), "SELECT 2", seen)
    assert f.read_text() == "SELECT 'someone else'"


def test_save_as_asks_before_replacing_a_file_and_makes_a_new_one(tmp_path):
    f = tmp_path / "q.sql"
    f.write_text("old")
    with pytest.raises(sql_files.Conflict, match="already there"):
        sql_files.write(str(f), "new")
    sql_files.write(str(f), "new", overwrite=True)
    assert f.read_bytes() == b"new"
    fresh = tmp_path / "fresh.sql"
    out = sql_files.write(str(fresh), "SELECT 1\nSELECT 2")
    assert fresh.read_bytes() == b"SELECT 1\r\nSELECT 2" and out["exists"] is True
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


@pytest.mark.parametrize("path,match", [
    ("q.sql", "full path"), ("", "full path"), (None, "full path"),
    ("C:/x/notes.txt" if os.name == "nt" else "/x/notes.txt", "Only .sql"),
])
def test_only_full_paths_of_sql_files(path, match):
    with pytest.raises(ValueError, match=match):
        sql_files.read(path)


def test_links_and_large_or_unreadable_files_are_refused(tmp_path, monkeypatch):
    real = tmp_path / "real.sql"
    real.write_text("SELECT 1")
    link = tmp_path / "link.sql"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("this PC can't make a symbolic link")
    with pytest.raises(ValueError, match="link"):
        sql_files.read(str(link))
    with pytest.raises(ValueError, match="link"):
        sql_files.write(str(link), "SELECT 2", overwrite=True)
    assert real.read_text() == "SELECT 1"
    bad = tmp_path / "bad.sql"
    bad.write_bytes(b"SELECT \x93x\x94")  # cp1252 quotes
    with pytest.raises(ValueError, match="isn't UTF-8"):
        sql_files.read(str(bad))
    monkeypatch.setattr(sql_files, "MAX_BYTES", 4)
    with pytest.raises(ValueError, match="too large"):
        sql_files.read(str(real))
    with pytest.raises(ValueError, match="too large"):
        sql_files.write(str(tmp_path / "big.sql"), "SELECT 12345")


def test_a_missing_file_says_so(tmp_path):
    assert sql_files.info(str(tmp_path / "gone.sql"))["exists"] is False
    with pytest.raises(ValueError, match="isn't there"):
        sql_files.read(str(tmp_path / "gone.sql"))
    with pytest.raises(sql_files.Conflict, match="isn't there"):
        sql_files.write(str(tmp_path / "gone.sql"), "x", expect="1-1")
    with pytest.raises(ValueError, match="folder"):
        sql_files.write(str(tmp_path / "no" / "x.sql"), "x")


def test_the_page_calls_are_listed_and_only_a_save_is_audited(tmp_path):
    for name in ("sql_file_info", "sql_file_read", "sql_file_write"):
        assert name in server.METHODS and name in server.QUICK
    assert "sql_file_read" in audit.READ_ONLY and "sql_file_info" in audit.READ_ONLY
    assert audit.audited("sql_file_write")
    f = tmp_path / "q.sql"
    f.write_text("x")
    said = Service.sql_file_write(None, str(f), "y")  # type: ignore[arg-type]
    assert said["conflict"] is True and "already there" in said["message"] and f.read_text() == "x"

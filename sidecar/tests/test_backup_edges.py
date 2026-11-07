"""Backups and restores: the edges the main tests don't reach. A database copy that stays locked or
can't be read, text that isn't UTF-8 or JSON, files that can't be opened, a backup with no manifest
or a name in it twice, the in-use check's refusals, and putting files back when a restore stops."""

from __future__ import annotations

import json
import logging
import sqlite3
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from fabric_desktop import backup


def sqlite_error(code):
    exc = sqlite3.OperationalError("database is locked")
    exc.sqlite_errorcode = code
    return exc


def test_a_database_that_stays_busy_or_cant_be_read_stops_the_backup(tmp_path, monkeypatch):
    db = tmp_path / "queue.sqlite3"
    db.write_bytes(b"x")
    monkeypatch.setattr(backup, "_sqlite_bytes", lambda p: (_ for _ in ()).throw(sqlite_error(sqlite3.SQLITE_BUSY)))
    with pytest.raises(backup.BackupError, match="was being written to"):
        backup._database_copy("queue.sqlite3", db)
    monkeypatch.setattr(backup, "_sqlite_bytes", lambda p: (_ for _ in ()).throw(sqlite_error(sqlite3.SQLITE_IOERR)))
    with pytest.raises(backup.BackupError, match="couldn't be read"):
        backup._database_copy("queue.sqlite3", db)


def test_text_that_isnt_utf8_or_json_is_kept_and_still_cleaned():
    assert backup._clean_text(PurePosixPath("a.json"), b"\xff\xfe") == (b"\xff\xfe", [])
    data, removed = backup._clean_text(PurePosixPath("a.json"), b"{not json")
    assert data == b"{not json" and removed == []
    data, removed = backup._clean_text(PurePosixPath("a.json"), json.dumps({"teams_webhook": "x", "a": 1}).encode())
    assert json.loads(data) == {"a": 1} and removed == ["teams_webhook"]


def test_a_schedule_that_isnt_json_is_kept_as_it_is():
    assert backup._forget_engine(b"not json") == b"not json"
    assert json.loads(backup._forget_engine(b'{"engine": "C:/py.exe", "id": 1}')) == {"id": 1}
    assert json.loads(backup._forget_engine(b"[1, 2]")) == [1, 2]


def test_a_file_that_cant_be_opened_has_no_sqlite_header(tmp_path):
    assert backup._has_sqlite_header(tmp_path / "missing.db") is False


def make_zip(path: Path, members: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members:
            z.writestr(name, data)
    return path


def test_a_zip_without_a_manifest_is_not_a_backup(tmp_path):
    z = make_zip(tmp_path / "b.zip", [("app/settings.json", b"{}")])
    assert backup.validate(z) == ({}, ["It has no readable manifest.json, so it isn't a KratosForge backup."])
    z = make_zip(tmp_path / "c.zip", [(backup.MANIFEST, b"{broken")])
    assert backup.validate(z)[1] == ["It has no readable manifest.json, so it isn't a KratosForge backup."]


def test_a_name_in_the_zip_twice_is_refused(tmp_path):
    data = b"{}"
    import hashlib
    entry = {"path": "app/settings.json", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    manifest = json.dumps({"schema": backup.SCHEMA, "created": "x", "files": [entry]}).encode()
    with pytest.warns(UserWarning, match="Duplicate name"):
        z = make_zip(tmp_path / "b.zip", [(backup.MANIFEST, manifest), ("app/settings.json", data), ("app/settings.json", data)])
    _, problems = backup.validate(z)
    assert "app/settings.json is in the zip twice" in problems


def test_in_use_check(tmp_path, monkeypatch):
    assert backup._in_use(tmp_path / "missing.db") is False
    plain = tmp_path / "plain.db"
    plain.write_bytes(b"not a database")
    assert backup._in_use(plain) is False

    db = tmp_path / "real.db"
    db.write_bytes(backup.SQLITE_HEADER + b"\x00" * 100)
    real_open = Path.open

    def unreadable(self, *a, **kw):
        if self == db:
            raise PermissionError("locked by another program")
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", unreadable)
    assert backup._in_use(db) is True  # can't even read it: treat as in use
    monkeypatch.setattr(Path, "open", real_open)

    def refuse(*a, **kw):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(backup.sqlite3, "connect", refuse)
    assert backup._in_use(db) is True


def test_put_back_restores_what_was_swapped_and_removes_what_was_staged(tmp_path, caplog):
    safety = tmp_path / "safety"
    safety.mkdir()
    # replaced: goes back to its copy
    replaced, kept = tmp_path / "a.json", safety / "a.json"
    replaced.write_text("new", encoding="utf-8")
    kept.write_text("old", encoding="utf-8")
    # added by the restore: goes away
    added = tmp_path / "b.json"
    added.write_text("added", encoding="utf-8")
    # not swapped yet (its .restoring is still there): left alone, its companion comes back
    pending = tmp_path / "c.db"
    pending.write_text("as it was", encoding="utf-8")
    staged = pending.with_name("c.db.restoring")
    staged.write_text("staged", encoding="utf-8")
    wal, wal_kept = tmp_path / "c.db-wal", safety / "c.db-wal"
    wal_kept.write_text("wal", encoding="utf-8")
    # one that can't be put back: logged, the rest still go back
    stuck = tmp_path / "d.json"
    stuck.write_text("new", encoding="utf-8")
    missing_copy = safety / "gone.json"

    with caplog.at_level(logging.WARNING, logger=backup.__name__):
        backup._put_back([(replaced, kept, []), (added, None, []), (pending, None, [(wal, wal_kept)]),
                          (stuck, missing_copy, [])], [staged])
    assert replaced.read_text(encoding="utf-8") == "old"
    assert not added.exists()
    assert pending.read_text(encoding="utf-8") == "as it was" and wal.read_text(encoding="utf-8") == "wal"
    assert not staged.exists()
    assert "couldn't put" in caplog.text and str(stuck) in caplog.text


def test_put_back_logs_a_staged_file_it_cant_remove(tmp_path, monkeypatch, caplog):
    staged = tmp_path / "x.restoring"
    staged.write_text("s", encoding="utf-8")
    real = Path.unlink

    def in_use(self, *a, **kw):
        if self == staged:
            raise PermissionError("in use")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", in_use)
    with caplog.at_level(logging.WARNING, logger=backup.__name__):
        backup._put_back([], [staged])
    assert "couldn't remove" in caplog.text

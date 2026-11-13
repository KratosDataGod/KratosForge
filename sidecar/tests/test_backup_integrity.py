"""Backup integrity: a damaged, tampered or wrong-version backup is refused before anything is written,
a restore that stops part way leaves the folder as it was, and a backup taken while the app writes is
consistent or isn't saved at all."""

import json
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fabric_desktop import backup, jsonfile

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "FabricDesktop"
    (root / "notebooks").mkdir(parents=True)
    (root / "notebooks" / "sales.ipynb").write_text('{"cells": []}')
    (root / "app-settings.json").write_text(json.dumps({"channel": "preview"}))
    with closing(sqlite3.connect(root / "workloads.sqlite")) as db:
        db.execute("create table w (n int)")
        db.executemany("insert into w values (?)", [(i,) for i in range(100)])
        db.commit()
    return root


def _state(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _rewrite(zip_path, out, change):
    """A copy of the backup with ``change(name, data, manifest)`` applied to each entry."""
    with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(out, "w") as dst:
        manifest = json.loads(src.read("manifest.json"))
        for name in src.namelist():
            if name != "manifest.json":
                data = change(name, src.read(name), manifest)
                if data is not None:
                    dst.writestr(name, data)
        dst.writestr("manifest.json", json.dumps(manifest))
    return out


def _schema_two(name, data, manifest):
    manifest["schema"] = "fabric-desktop-backup@2"
    return data


def _bigger(name, data, manifest):  # the zip holds more than the manifest says
    return data + b" " * 10 if name == "app/app-settings.json" else data


def _listed_twice(name, data, manifest):
    if not any(e["path"] == "app/APP-SETTINGS.json" for e in manifest["files"]):
        entry = next(e for e in manifest["files"] if e["path"] == "app/app-settings.json")
        manifest["files"].append(dict(entry, path="app/APP-SETTINGS.json"))
    return data


@pytest.mark.parametrize("damage", ["truncated", "bad crc", "wrong version", "size", "listed twice", "zip entry twice"])
def test_a_damaged_or_tampered_backup_is_refused_and_nothing_is_written(app, tmp_path, damage):
    zip_path = Path(backup.create(tmp_path / "out", app, now=NOW)["path"])
    bad = tmp_path / "bad.zip"
    if damage == "truncated":
        bad.write_bytes(zip_path.read_bytes()[: zip_path.stat().st_size // 2])
    elif damage == "bad crc":  # stored, so a flipped byte changes the data, not the compression
        with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(bad, "w", zipfile.ZIP_STORED) as dst:
            for name in src.namelist():
                dst.writestr(name, src.read(name))
        raw = bytearray(bad.read_bytes())
        at = raw.find(b'{"channel"')
        raw[at + 2] ^= 0x01
        bad.write_bytes(bytes(raw))
    elif damage == "wrong version":
        _rewrite(zip_path, bad, _schema_two)
    elif damage == "size":
        _rewrite(zip_path, bad, _bigger)
    elif damage == "listed twice":
        _rewrite(zip_path, bad, _listed_twice)
    else:
        bad.write_bytes(zip_path.read_bytes())
        with zipfile.ZipFile(bad, "a") as z, pytest.warns(UserWarning, match="Duplicate name"):
            z.writestr("app/app-settings.json", '{"channel": "stable"}')
    (app / "app-settings.json").write_text(json.dumps({"channel": "stable"}))
    before = _state(app)
    _, problems = backup.validate(bad)
    assert problems, damage
    with pytest.raises(backup.BackupError, match="can't be restored"):
        backup.restore(bad, app, apply=True, now=NOW)
    assert _state(app) == before


def test_a_restore_that_stops_part_way_puts_every_file_back(app, tmp_path, monkeypatch):
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    (app / "notebooks" / "sales.ipynb").write_text('{"cells": ["mine"]}')
    (app / "app-settings.json").write_text(json.dumps({"channel": "stable"}))
    with closing(sqlite3.connect(app / "workloads.sqlite")) as db:
        db.execute("delete from w where n > 9")
        db.commit()
    before = _state(app)
    real, swaps = jsonfile.replace, []

    def full_disk(src, dst):
        swaps.append(dst)
        if len(swaps) == 3:
            raise OSError(28, "No space left on device")
        real(src, dst)

    monkeypatch.setattr(backup.jsonfile, "replace", full_disk)
    with pytest.raises(OSError, match="No space"):
        backup.restore(zip_path, app, apply=True, now=NOW)
    assert len(set(swaps[:3])) == 3  # two files had been swapped in when the third stopped it; then put back
    after = {k: v for k, v in _state(app).items() if not k.startswith(backup.SAFETY)}
    assert after == before


def test_a_backup_waits_a_bounded_time_for_a_locked_database_then_saves_nothing(app, tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "DB_WAIT", 0.3)
    writer = sqlite3.connect(app / "workloads.sqlite", isolation_level=None)
    try:
        writer.execute("begin exclusive")  # rollback-journal database held mid-write
        writer.execute("insert into w values (-1)")
        with pytest.raises(backup.BackupError, match="workloads.sqlite was being written to"):
            backup.create(tmp_path / "out", app, now=NOW)
    finally:
        writer.execute("rollback")
        writer.close()
    assert list((tmp_path / "out").iterdir()) == []  # no zip and no .part left behind


def test_a_backup_while_a_wal_database_is_written_holds_only_committed_rows(app, tmp_path):
    writer = sqlite3.connect(app / "workloads.sqlite", isolation_level=None)
    try:
        writer.execute("pragma journal_mode=wal")
        writer.execute("insert into w values (100)")  # committed, still only in the WAL
        writer.execute("begin immediate")
        writer.execute("insert into w values (-1)")  # not committed
        zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    finally:
        writer.execute("rollback")
        writer.close()
    copy = tmp_path / "copy.sqlite"
    with zipfile.ZipFile(zip_path) as z:
        copy.write_bytes(z.read("app/workloads.sqlite"))
    with closing(sqlite3.connect(copy)) as db:
        assert db.execute("pragma integrity_check").fetchone() == ("ok",)
        assert db.execute("select count(*), min(n), max(n) from w").fetchone() == (101, 0, 100)


def test_a_database_without_a_database_name_is_still_copied_consistently(app, tmp_path):
    with closing(sqlite3.connect(app / "state.bin")) as db:
        db.execute("pragma journal_mode=wal")
        db.execute("create table s (n int)")
        db.execute("insert into s values (1)")
        db.commit()
        zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]  # while its WAL holds the rows
    copy = tmp_path / "copy.bin"
    with zipfile.ZipFile(zip_path) as z:
        copy.write_bytes(z.read("app/state.bin"))
    with closing(sqlite3.connect(copy)) as db:
        assert db.execute("select n from s").fetchall() == [(1,)]


def test_a_line_still_being_appended_is_left_out(app, tmp_path):
    (app / "history.jsonl").write_bytes(b'{"run": 1}\n{"run": 2}\n{"ru')
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    with zipfile.ZipFile(zip_path) as z:
        assert z.read("app/history.jsonl") == b'{"run": 1}\n{"run": 2}\n'


def test_a_backup_that_stops_part_way_leaves_no_zip(app, tmp_path, monkeypatch):
    def fails(*args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup, "_fill", fails)
    with pytest.raises(OSError):
        backup.create(tmp_path / "out", app, now=NOW)
    assert list((tmp_path / "out").iterdir()) == []

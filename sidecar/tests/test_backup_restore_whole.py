"""A restore leaves the app's folder as it was or as the backup has it, never a mix: whichever file
it stops at, whatever stops it."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from fabric_desktop import backup, jsonfile
from test_backup import NOW, _rows, app  # noqa: F401  (app is the fixture)


def _state(root):
    """Every file under the app folder but the restore's own safety copies, with its bytes."""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and backup.SAFETY not in p.relative_to(root).parts}


@pytest.fixture
def newer(app, tmp_path):  # noqa: F811
    """A backup, then a store that moved on: every backed-up file changed, and one added. Gives the
    backup, the app folder as it is now (kept aside to copy from), and what a finished restore gives."""
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    for item in backup.restore(zip_path, app)["files"]:
        p = Path(item["to"])
        if p.suffix != ".sqlite":
            p.write_bytes(p.read_bytes() + b"\n-- newer")
    with closing(sqlite3.connect(app / "workloads.sqlite")) as db:
        db.execute("insert into w values ('newer')")
        db.commit()
    (app / "notebooks" / "new.ipynb").write_text("{}")
    pristine = tmp_path / "pristine"
    shutil.copytree(app, pristine)
    done = tmp_path / "done"
    shutil.copytree(app, done)
    backup.restore(zip_path, done, apply=True, now=NOW)
    return zip_path, pristine, _state(done)


def _copy(pristine, tmp_path, n):
    folder = tmp_path / f"run{n}"
    shutil.copytree(pristine, folder)
    return folder


def test_a_full_restore_gives_the_backup_and_keeps_the_rest(newer, tmp_path):
    zip_path, pristine, expected = newer
    folder = _copy(pristine, tmp_path, 0)
    assert expected != _state(folder)
    backup.restore(zip_path, folder, apply=True, now=NOW)
    assert _state(folder) == expected


def test_a_file_that_doesnt_write_correctly_changes_nothing_whichever_it_is(newer, tmp_path, monkeypatch):
    zip_path, pristine, _ = newer
    plan = backup.restore(zip_path, pristine)["files"]
    assert len(plan) >= 4
    real = backup._sha256
    for n, item in enumerate(plan):
        folder = _copy(pristine, tmp_path, n)
        before = _state(folder)
        bad = Path(item["to"]).name + ".restoring"
        monkeypatch.setattr(backup, "_sha256", lambda p, bad=bad: "0" * 64 if p.name == bad else real(p))
        with pytest.raises(backup.BackupError, match="nothing was changed"):
            backup.restore(zip_path, folder, apply=True, now=NOW)
        assert _state(folder) == before, item["path"]


def test_a_swap_that_fails_part_way_puts_back_the_files_already_swapped(newer, tmp_path, monkeypatch):
    zip_path, pristine, _ = newer
    plan = backup.restore(zip_path, pristine)["files"]
    real = jsonfile.replace
    for n in range(len(plan)):
        folder = _copy(pristine, tmp_path, n)
        before, calls = _state(folder), []

        def swap(src, dst, n=n, calls=calls):
            calls.append(dst)
            if len(calls) == n + 1:
                raise OSError("the disk went away")
            real(src, dst)
        monkeypatch.setattr(jsonfile, "replace", swap)
        with pytest.raises(OSError, match="disk went away"):
            backup.restore(zip_path, folder, apply=True, now=NOW)
        monkeypatch.setattr(jsonfile, "replace", real)
        assert _state(folder) == before, plan[n]["path"]


def test_a_stopped_restore_puts_back_a_wal_it_moved_aside(app, tmp_path, monkeypatch):  # noqa: F811
    runs = app / "runs.sqlite"
    with closing(sqlite3.connect(runs)) as db:
        db.execute("pragma journal_mode=wal")
        db.execute("create table r (id int)")
        db.execute("insert into r values (1)")
        db.commit()
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    with closing(sqlite3.connect(runs)) as db:  # written after the backup and still only in the WAL
        db.execute("pragma wal_autocheckpoint=0")
        db.executemany("insert into r values (?)", [(i,) for i in range(200)])
        db.commit()
        wal = (app / "runs.sqlite-wal").read_bytes()
    (app / "runs.sqlite-wal").write_bytes(wal)
    before = _state(app)

    real = jsonfile.replace

    def swap(src, dst):
        if Path(dst).name == "runs.sqlite":
            raise OSError("the disk went away")
        real(src, dst)
    monkeypatch.setattr(jsonfile, "replace", swap)
    with pytest.raises(OSError):
        backup.restore(zip_path, app, apply=True, now=NOW)
    monkeypatch.setattr(jsonfile, "replace", real)
    assert _state(app) == before
    assert _rows(runs) == (201, {"r"})  # the WAL is back beside its database, nothing lost

"""Mutation batch 151, a re-score of backup.py from batch 4 (docs/quality/mutation-testing.md): a
member inside a skipped folder is refused, a restore makes nested folders, a database's stray WAL
moved aside keeps restore-safety, and a restore that stops part way leaves no warning about files
it already swapped."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fabric_desktop import backup

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def _backup(tmp_path: Path, files: dict[str, str]) -> Path:
    src = tmp_path / "src"
    for rel, text in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
    src.mkdir(exist_ok=True)
    return Path(backup.create(tmp_path / "out", src, now=NOW)["path"])


def test_a_member_inside_a_skipped_folder_is_refused():
    # The skip rules apply to the path under app/ or agent/, not to the whole member name.
    assert backup._safe("app/logs/run.txt") is False
    assert backup._safe("app/notebooks/run.txt") is True


def test_a_restore_makes_the_nested_folders_it_needs(tmp_path):
    zip_path = _backup(tmp_path, {"notebooks/team/q4/sales.ipynb": '{"cells": []}'})
    target = tmp_path / "fresh"
    target.mkdir()
    backup.restore(zip_path, target, apply=True, now=NOW)
    assert (target / "notebooks" / "team" / "q4" / "sales.ipynb").read_text() == '{"cells": []}'


def test_a_stray_wal_moved_aside_keeps_its_copy_in_restore_safety(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    with closing(sqlite3.connect(src / "state.sqlite3")) as db:
        db.execute("CREATE TABLE t (x)")
        db.commit()
    zip_path = Path(backup.create(tmp_path / "out", src, now=NOW)["path"])
    target = tmp_path / "fresh"
    target.mkdir()
    # No database here yet, only a WAL left from one: it must not be replayed onto the restored file.
    (target / "state.sqlite3-wal").write_bytes(b"stale")
    out = backup.restore(zip_path, target, apply=True, now=NOW)
    assert not any(f["replaces"] for f in out["files"])
    assert out["safety"] == str(target / backup.SAFETY / "20261118-120000")
    assert (Path(out["safety"]) / "app" / "state.sqlite3-wal").read_bytes() == b"stale"
    assert not (target / "state.sqlite3-wal").exists()


def test_a_restore_that_stops_part_way_doesnt_warn_about_files_it_swapped(tmp_path, monkeypatch, caplog):
    zip_path = _backup(tmp_path, {"a.json": "{}", "b.json": "[]"})
    target = tmp_path / "fresh"
    target.mkdir()
    real = backup.jsonfile.replace
    calls = []

    def second_refused(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise PermissionError("refused")
        real(src, dst)

    monkeypatch.setattr(backup.jsonfile, "replace", second_refused)
    with caplog.at_level(logging.WARNING, logger=backup.__name__), pytest.raises(PermissionError):
        backup.restore(zip_path, target, apply=True, now=NOW)
    assert not [r for r in caplog.records if "couldn't" in r.getMessage()]
    assert sorted(p.name for p in target.iterdir()) == []

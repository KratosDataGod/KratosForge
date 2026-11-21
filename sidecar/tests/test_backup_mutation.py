"""backup.py: behavior that mutation testing (docs/quality/mutation-testing.md, batch 4) showed no test
pinned. How many restores' safety copies are kept and which folders pruning may touch, a top-level file
that only shares a skipped folder's name, an empty database file, a database known only by its header,
the output folder, a backup that stops before its .part file exists, and the restore summary."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

from fabric_desktop import backup

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "FabricDesktop"
    (root / "notebooks").mkdir(parents=True)
    (root / "notebooks" / "sales.ipynb").write_text('{"cells": []}')
    (root / "app-settings.json").write_text(json.dumps({"channel": "preview"}))
    return root


def _stamps(folder, n):
    for i in range(n):
        (folder / f"2026010{i}-120000").mkdir(parents=True)


def test_pruning_keeps_the_newest_five_restores_by_default(tmp_path):
    _stamps(tmp_path, 7)
    gone = backup._prune_safety(tmp_path)
    assert gone == ["20260100-120000", "20260101-120000"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"2026010{i}-120000" for i in range(2, 7)]


def test_pruning_to_one_keeps_the_newest(tmp_path):
    _stamps(tmp_path, 3)
    assert backup._prune_safety(tmp_path, keep=1) == ["20260100-120000", "20260101-120000"]
    assert [p.name for p in tmp_path.iterdir()] == ["20260102-120000"]


def test_pruning_leaves_folders_restore_didnt_name_and_files_with_a_restore_name(tmp_path):
    _stamps(tmp_path, 1)
    (tmp_path / "notes").mkdir()
    (tmp_path / "20250101-120000").write_text("a file, not a restore's folder")
    assert backup._prune_safety(tmp_path, keep=0) == ["20260100-120000"]
    assert (tmp_path / "notes").is_dir() and (tmp_path / "20250101-120000").is_file()


def test_pruning_a_missing_folder_removes_nothing(tmp_path):
    assert backup._prune_safety(tmp_path / "nope") == []


@pytest.mark.parametrize("name", ["logs", "engine", "onelake-cache"])
def test_a_top_level_file_named_like_a_skipped_folder_is_backed_up(name):
    assert backup._skipped(PurePosixPath(name)) is None
    assert backup._skipped(PurePosixPath(name) / "x.txt") == "rebuilt by the app"


def test_an_empty_database_file_goes_in_as_a_database(app, tmp_path):
    (app / "empty.db").write_bytes(b"")  # what sqlite3.connect leaves before the first table
    result = backup.create(tmp_path / "out", app, now=NOW)
    with zipfile.ZipFile(result["path"]) as z:
        assert z.read("app/empty.db").startswith(backup.SQLITE_HEADER)


def test_backups_go_into_a_new_nested_folder_and_again_into_the_same_one(app, tmp_path):
    out = tmp_path / "a" / "b" / "c"
    first = backup.create(out, app, now=NOW)
    second = backup.create(out, app, now=datetime(2026, 10, 17, 12, 1, tzinfo=timezone.utc))
    assert {p.name for p in out.iterdir()} == {Path(first["path"]).name, Path(second["path"]).name}


def test_a_backup_that_stops_before_its_part_file_exists_raises_its_own_error(app, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("stopped before writing")
    monkeypatch.setattr(backup, "_write", boom)
    with pytest.raises(RuntimeError, match="stopped before writing"):
        backup.create(tmp_path / "out", app, now=NOW)
    assert list((tmp_path / "out").iterdir()) == []


def test_a_restore_that_replaces_and_moves_nothing_keeps_no_safety_copy(app, tmp_path):
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    fresh = tmp_path / "fresh"
    done = backup.restore(zip_path, fresh, apply=True, now=NOW)
    assert done["applied"] and done["safety"] is None and done["pruned"] == []
    assert (fresh / "notebooks" / "sales.ipynb").read_text() == '{"cells": []}'


def test_a_restore_keeps_safety_copies_of_several_files_in_one_nested_folder(app, tmp_path):
    (app / "notebooks" / "b.ipynb").write_text('{"cells": ["b"]}')
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    (app / "notebooks" / "sales.ipynb").write_text("changed")
    (app / "notebooks" / "b.ipynb").write_text("changed too")
    backup.restore(zip_path, app, apply=True, now=NOW)
    kept = app / backup.SAFETY / "20261017-120000" / "app" / "notebooks"
    assert (kept / "sales.ipynb").read_text() == "changed"
    assert (kept / "b.ipynb").read_text() == "changed too"


def test_a_database_known_only_by_its_header_has_its_stale_wal_moved_aside(app, tmp_path):
    state = app / "state.bin"  # a database without a database suffix
    with closing(sqlite3.connect(state)) as db:
        db.execute("create table t (x int)")
        db.commit()
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    (app / "state.bin-wal").write_bytes(b"stale")
    done = backup.restore(zip_path, app, apply=True, now=NOW)
    assert not (app / "state.bin-wal").exists()
    assert (app / backup.SAFETY / "20261017-120000" / "app" / "state.bin-wal").read_bytes() == b"stale"
    assert done["safety"]


def test_the_summary_counts_replaced_files_and_says_whether_it_restored(app, tmp_path):
    zip_path = backup.create(tmp_path / "out", app, now=NOW)["path"]
    (app / "notebooks" / "sales.ipynb").unlink()
    preview = backup.restore(zip_path, app)
    text = backup.describe(preview)
    assert text.startswith("Would restore 2 files from the backup of 2026-10-17T12:00:00+00:00 "
                           "(1 replace a file already here).")
    assert "Nothing was changed." in text
    done = backup.describe(backup.restore(zip_path, app, apply=True, now=NOW))
    assert done.startswith("Restored 2 files")
    assert "Nothing was changed." not in done

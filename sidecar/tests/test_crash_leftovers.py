"""What a local Delta write that was stopped part way (the app killed, the PC switched off) leaves in
the table folder, and how health reports it and Compact removes it.

delta-rs writes each data file as <name>#<n> and renames it into place, then writes the commit as
NNN.json#<n> and links it into place. Killing the writer between those steps leaves, depending on
when: a staged data file, a data file no commit names, or a staged commit next to (or instead of)
its published one. The table itself stays at its last good version in each case.
"""

import os
import shutil
import time
import uuid
from pathlib import Path

import polars as pl
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import maintenance

HOUR = 3600


def _age(path: Path, hours: float) -> None:
    then = time.time() - hours * HOUR
    os.utime(path, (then, then))


def _stopped_writes(tmp_path: Path) -> tuple[str, dict[str, Path]]:
    """A two-version table plus what killed writes left in it, each aged as named."""
    uri = str(tmp_path / "t")
    write_deltalake(uri, pl.DataFrame({"id": [1, 2, 3]}).to_arrow())
    write_deltalake(uri, pl.DataFrame({"id": [4]}).to_arrow(), mode="append")
    root = Path(uri)
    committed = sorted(root.glob("*.parquet"))
    log = root / "_delta_log"
    left = {
        # killed after a data file was in place, before the commit: no version names it
        "old_orphan": root / f"part-00000-{uuid.uuid4()}-c000.snappy.parquet",
        "new_orphan": root / f"part-00000-{uuid.uuid4()}-c000.snappy.parquet",
        # killed while a data file was still under its staging name
        "staged_data": root / f"part-00000-{uuid.uuid4()}-c000.snappy.parquet#1",
        # killed mid commit: the commit written under its staging name, never linked
        "staged_commit": log / "00000000000000000002.json#1",
        # killed after the link, before the staged copy was removed
        "linked_copy": log / "00000000000000000001.json#1",
        # a write under way right now: just staged
        "live_staged": root / f"part-00000-{uuid.uuid4()}-c000.snappy.parquet#1",
    }
    for name in ("old_orphan", "new_orphan", "staged_data", "live_staged"):
        shutil.copy(committed[0], left[name])
    left["staged_commit"].write_text('{"commitInfo":{"operation":"WRITE"}}\n{"add":{"path":"part-00000-dead')
    shutil.copy(log / "00000000000000000001.json", left["linked_copy"])
    _age(left["old_orphan"], 10 * 24)
    _age(left["new_orphan"], 2)
    for name in ("staged_data", "staged_commit", "linked_copy"):
        _age(left[name], 2)
    return uri, left


def _rows(uri: str) -> list[int]:
    return sorted(pl.from_arrow(DeltaTable(uri).to_pyarrow_table())["id"].to_list())


def test_a_table_a_stopped_write_left_files_in_still_reads_at_its_last_version(tmp_path):
    uri, _ = _stopped_writes(tmp_path)
    t = DeltaTable(uri)
    assert t.version() == 1
    assert _rows(uri) == [1, 2, 3, 4]


def test_health_on_this_pc_reports_what_stopped_writes_left(tmp_path):
    uri, left = _stopped_writes(tmp_path)
    h = maintenance.health(uri, local=True)
    found = set(maintenance.leftovers(uri))
    rel = {k: p.relative_to(uri).as_posix() for k, p in left.items()}
    assert found == {rel["old_orphan"], rel["new_orphan"], rel["staged_data"], rel["staged_commit"], rel["linked_copy"]}
    assert h["leftover_files"] == 5 and h["status"] == "needs_maintenance"
    assert any(a.startswith("5 files (") and "stopped part way" in a for a in h["advice"])
    # A table in OneLake isn't listed this way: another engine's write there may still be under way.
    assert "stopped part way" not in " ".join(maintenance.health(uri)["advice"])


def test_compact_removes_what_stopped_writes_left_and_nothing_a_write_still_needs(tmp_path):
    uri, left = _stopped_writes(tmp_path)
    result = maintenance.compact_local(uri, days=7)
    assert result["staged_removed"] == 3 and result["unnamed_removed"] == 1
    assert not left["staged_data"].exists() and not left["staged_commit"].exists() and not left["linked_copy"].exists()
    assert not left["old_orphan"].exists()  # past the 7 days of history kept
    # A data file no commit names yet may belong to a long write still under way: it stays as long
    # as the history does, as Delta's own VACUUM keeps it. A file just staged is a write under way.
    assert left["new_orphan"].exists() and left["live_staged"].exists()
    assert _rows(uri) == [1, 2, 3, 4]
    assert sorted(pl.from_arrow(DeltaTable(uri, version=1).to_pyarrow_table())["id"].to_list()) == [1, 2, 3, 4]
    # Version 2 was never published, so the next write takes it.
    write_deltalake(uri, pl.DataFrame({"id": [5]}).to_arrow(), mode="append")
    assert _rows(uri) == [1, 2, 3, 4, 5]
    assert maintenance.leftovers(uri) == [left["new_orphan"].name]


def test_a_healthy_local_table_reports_no_leftovers(tmp_path):
    uri = str(tmp_path / "t")
    write_deltalake(uri, pl.DataFrame({"id": [1]}).to_arrow())
    write_deltalake(uri, pl.DataFrame({"id": [2]}).to_arrow(), mode="overwrite")
    for p in Path(uri).rglob("*"):
        if p.is_file():
            _age(p, 10 * 24)
    h = maintenance.health(uri, local=True)
    assert h["leftover_files"] == 0 and not any("stopped part way" in a for a in h["advice"])
    assert maintenance.leftovers(uri) == []  # the overwritten file is VACUUM's, not a leftover


def _link_folder(link: Path, target: Path) -> None:
    """A symbolic link, or on Windows without the right to make one, a junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def test_compact_never_deletes_through_a_linked_folder(tmp_path):
    uri, left = _stopped_writes(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    kept = [outside / f"part-00000-{uuid.uuid4()}-c000.snappy.parquet", outside / "notes.txt",
            outside / f"part-00000-{uuid.uuid4()}-c000.snappy.parquet#1"]
    for p in kept:
        p.write_text("not the table's")
        _age(p, 30 * 24)
    _link_folder(Path(uri) / "linked", outside)
    assert all(not p.startswith("linked") for p in maintenance.leftovers(uri))
    maintenance.compact_local(uri, days=7)
    assert all(p.exists() for p in kept)
    # The table's own leftovers still go.
    assert not left["staged_data"].exists() and not left["staged_commit"].exists() and not left["old_orphan"].exists()
    assert _rows(uri) == [1, 2, 3, 4]


def test_files_that_aren_t_the_table_s_are_never_reported_or_removed(tmp_path):
    uri, left = _stopped_writes(tmp_path)
    root = Path(uri)
    (root / "p=1").mkdir()
    foreign = [root / "notes.txt", root / "desktop.ini", root / "p=1" / "data.csv", root / "p=1" / "part-x.parquet",
               root / "copy of part.parquet", root / "readme.md#1", root / "_delta_log" / "notes.json#1"]
    for p in foreign:
        p.write_text("someone else's")
        _age(p, 30 * 24)
    found = maintenance.leftovers(uri)
    assert not {p.relative_to(root).as_posix() for p in foreign} & set(found)
    maintenance.compact_local(uri, days=7)
    assert all(p.exists() for p in foreign)
    assert not left["old_orphan"].exists() and not left["staged_data"].exists()

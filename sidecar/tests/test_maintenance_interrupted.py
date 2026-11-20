"""OPTIMIZE (compaction), Z-Order and VACUUM run again after one was stopped part way, on a table on this PC.

Each way maintenance runs here is covered: the Maintain page's Optimize (maintenance.compact_local: compaction,
the retention properties, VACUUM, then the unnamed-file cleanup), and a notebook's OPTIMIZE, OPTIMIZE ... ZORDER BY
and VACUUM (lakehouse.maintain_table). A stopped run is simulated by leaving on disk what it would have left:
data files it wrote but never committed (copies of real data files under new names, and a parquet file cut off
part way), the files a VACUUM deleted before it died, or a compaction committed without the VACUUM after it. One
test also kills a real compaction in another process. Time is never waited on: a file's age is its mtime, and a
removal's is its commit's deletionTimestamp (test_vacuum_hardening's helpers).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import unquote

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, maintenance
from tests.test_spark_writer import lake  # noqa: F401 - a fixture
from tests.test_vacuum_hardening import _age, _age_removes, _orphan
from tests.test_zorder_hardening import allowed, child, events, ids, wait_for  # noqa: F401 - allowed is a fixture

KINDS = ["maintain-page", "notebook-optimize", "notebook-zorder"]
OLD_HOURS = 30 * 24  # well past the 7 days (and the hour of margin) of history kept


def optimize(kind: str, uri: str) -> dict | lakehouse.MaintenanceResult:
    if kind == "maintain-page":
        return maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)
    return lakehouse.maintain_table("dbo.events", "optimize",
                                    zorder=["amount", "id"] if kind == "notebook-zorder" else None)


def every_version(uri: str, first: int = 0) -> dict[int, list[int]]:
    """Each version's ids from ``first`` on, sorted: every one must still read."""
    return {v: sorted(ids(uri, v)) for v in range(first, DeltaTable(uri).version() + 1)}


def named(uri: str, version: int | None = None) -> set[str]:
    return {unquote(u.rsplit("/", 1)[-1]) for u in DeltaTable(uri, version=version).file_uris()}


def vacuumed(files: list[str]) -> set[str]:
    """A notebook VACUUM's file list by name: delta-rs gives the files it deleted as full paths, and the ones
    already gone as paths relative to the table folder."""
    return {unquote(f).replace("\\", "/").rsplit("/", 1)[-1] for f in files}


def truncated(uri: str, hours: float) -> Path:
    """A data file cut off part way, as a write killed while writing it leaves: no footer, unreadable."""
    root = Path(uri)
    real = next(p for p in root.glob("*.parquet") if p.name in named(uri))
    path = root / f"part-00000-{uuid.uuid4()}-c000.snappy.parquet"
    path.write_bytes(real.read_bytes()[: real.stat().st_size // 2])
    _age(path, hours)
    return path


def uncommitted(uri: str, hours: float) -> list[Path]:
    """What a rewrite that wrote its files and died before its commit leaves: two copies of a real data file
    under new names (read, they would double rows) and one cut off part way (read, it would fail)."""
    return [_orphan(uri, hours), _orphan(uri, hours), truncated(uri, hours)]


def assert_same_rows(uri: str, expected: list[int]) -> None:
    got = ids(uri)
    assert len(got) == len(set(got)), "duplicated rows"
    assert sorted(got) == expected


# ---- 1. a rewrite that wrote its files and died before its commit

@pytest.mark.parametrize("kind", KINDS)
def test_a_rewrite_that_died_before_its_commit_leaves_files_the_next_run_never_reads(allowed, kind):
    """The next run succeeds and reads exactly the committed rows; the dead run's files are never read (copies
    would double rows, the cut-off one would fail). A file of it younger than the retention stays: it could be
    another writer's still in flight. One older goes only by compact_local's unnamed-file cleanup; a notebook's
    OPTIMIZE and VACUUM (delta-rs's lite VACUUM, which deletes only files a commit removed) leave it."""
    uri = events(allowed, 12)
    expected, history = sorted(ids(uri)), every_version(uri)
    young, old = uncommitted(uri, 0), uncommitted(uri, OLD_HOURS)
    out = optimize(kind, uri)
    assert_same_rows(uri, expected)
    assert not named(uri) & {p.name for p in young + old}  # the new version names none of them
    assert DeltaTable(uri).version() > max(history)  # it did rewrite
    assert {v: rows for v, rows in every_version(uri).items() if v in history} == history
    assert all(p.exists() for p in young)
    if kind == "maintain-page":
        assert out["unnamed_removed"] == len(old) and not any(p.exists() for p in old), out
    else:
        assert all(p.exists() for p in old)
        vac = lakehouse.maintain_table("dbo.events", "vacuum")
        assert vac.files == [] and all(p.exists() for p in young + old), vac
    # and again, after that
    out = optimize(kind, uri)
    assert_same_rows(uri, expected)
    assert {v: rows for v, rows in every_version(uri).items() if v in history} == history
    assert all(p.exists() for p in young)
    if kind == "maintain-page":
        assert out["unnamed_removed"] == 0 and out["vacuumed"] == 0, out
    else:
        assert lakehouse.maintain_table("dbo.events", "vacuum").files == []


# ---- 2. a VACUUM that died after deleting some of its files

@pytest.mark.parametrize("kind", ["maintain-page", "notebook-vacuum"])
def test_a_vacuum_that_died_part_way_finishes_on_the_next_run_and_keeps_every_version_inside_the_retention(
        allowed, kind):
    """Six appends (versions 0-5), a compaction whose removals are dated 10 days ago (6), two more appends (7, 8)
    and a compaction now (9). VACUUM at 7 days deletes the six first files; one that died deleted three of them.
    VACUUM again: no error on the missing three, the other three go, and versions 6 to the latest still read."""
    uri = events(allowed, 6)
    DeltaTable(uri).optimize.compact()
    _age_removes(uri, 6, 10)
    for b in range(6, 8):
        write_deltalake(uri, pa.table({"id": list(range(b * 50, b * 50 + 50)), "region": ["EU"] * 50,
                                       "amount": [0.0] * 50}), mode="append")
    DeltaTable(uri).optimize.compact()
    inside = every_version(uri, 6)  # 6 to 9: inside the 7 days
    expected = sorted(ids(uri))
    doomed = maintenance.vacuum_preview(uri, maintenance.MIN_RETENTION_DAYS)
    assert sorted(doomed) == sorted(named(uri, 0) | named(uri, 5)) and len(doomed) == 6, doomed
    gone, left = doomed[:3], doomed[3:]
    for rel in gone:
        (Path(uri) / rel).unlink()
    if kind == "maintain-page":
        out = maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)
        assert out["vacuumed"] == len(left), out  # counted from what it removed, not the missing three
    else:
        vac = lakehouse.maintain_table("dbo.events", "vacuum")
        listed = vacuumed(vac.files)
        assert set(left) <= listed <= set(doomed), listed  # delta-rs also lists the ones already gone
        # every file relative to the table, and counted from what this VACUUM deleted, not the missing three
        assert all(not f.startswith(uri) for f in vac.files) and vac.metrics["numDeletedFiles"] == len(left), vac
    assert not any((Path(uri) / rel).exists() for rel in doomed)
    # delta-rs keeps listing a removal it already deleted until a checkpoint drops it: a dry run still names
    # them, but none of them is on disk any more
    assert set(maintenance.vacuum_preview(uri, maintenance.MIN_RETENTION_DAYS)) <= set(doomed)
    assert {v: rows for v, rows in every_version(uri, 6).items() if v in inside} == inside
    assert_same_rows(uri, expected)
    # and again: nothing left to delete, and no error over the files already gone
    files = set(os.listdir(uri))
    if kind == "maintain-page":
        assert maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)["vacuumed"] == 0
    else:
        again = lakehouse.maintain_table("dbo.events", "vacuum")
        assert vacuumed(again.files) <= set(doomed) and again.metrics["numDeletedFiles"] == 0, again
    assert set(os.listdir(uri)) == files
    assert {v: rows for v, rows in every_version(uri, 6).items() if v in inside} == inside
    assert_same_rows(uri, expected)


# ---- 3. a real compaction killed in another process

KILLED = """
    import sys
    from deltalake import DeltaTable
    from fabric_desktop import maintenance
    uri, kind = sys.argv[1], sys.argv[2]
    if kind == "maintain-page":
        maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)
    else:
        DeltaTable(uri).optimize.z_order(["amount", "id"])
"""
BIG_BATCH = 20_000


@pytest.mark.parametrize("kind", ["maintain-page", "notebook-zorder"])
def test_a_compaction_killed_in_another_process_leaves_the_table_whole_and_the_next_run_works(allowed, kind):
    """Best effort: the kill can't be forced to a point. It comes as soon as a new data file (or its staged
    copy) appears in the table folder, which can be before the commit or, on a fast PC, after it (or the child
    may have finished). Either way the table reads the same rows at its latest version, every version reads,
    and Optimize run again works and changes no rows."""
    uri = str(allowed / "dbo" / "events")
    for b in range(30):
        start = b * BIG_BATCH
        write_deltalake(uri, pa.table({"id": list(range(start, start + BIG_BATCH)),
                                       "region": [("EU", "US", "APAC")[i % 3] for i in range(BIG_BATCH)],
                                       "amount": [float((i * 7919) % 1000) for i in range(BIG_BATCH)]}),
                        mode="append")
    expected, history = sorted(ids(uri)), every_version(uri)
    before = set(os.listdir(uri))
    p = child(KILLED, uri, kind)
    try:
        wait_for(lambda: p.poll() is not None or bool(set(os.listdir(uri)) - before), "a new data file")
    finally:
        p.kill()
        p.communicate(timeout=60)
    assert_same_rows(uri, expected)
    assert {v: rows for v, rows in every_version(uri).items() if v in history} == history
    assert all(rows == expected for v, rows in every_version(uri).items() if v not in history)
    optimize(kind, uri)
    assert_same_rows(uri, expected)
    assert {v: rows for v, rows in every_version(uri).items() if v in history} == history
    optimize(kind, uri)  # and once more
    assert_same_rows(uri, expected)


# ---- 4. stopped between compaction and VACUUM in compact_local

def test_compact_local_after_a_compaction_whose_vacuum_never_ran_changes_no_rows(allowed):
    uri = events(allowed, 10)
    expected = sorted(ids(uri))
    DeltaTable(uri).optimize.compact()  # compact_local's first step, and then it stopped
    history = every_version(uri)
    out = maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)
    assert out["files_added"] == 0 and out["files_removed"] == 0 and out["files_before"] == out["files_after"] == 1
    assert out["vacuumed"] == 0, out  # the compacted-away files were removed just now, inside the 7 days
    assert_same_rows(uri, expected)
    assert {v: rows for v, rows in every_version(uri).items() if v in history} == history
    out = maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)
    assert out["files_added"] == 0 and out["vacuumed"] == 0, out
    assert_same_rows(uri, expected)


# ---- what a notebook VACUUM reports it deleted

@pytest.mark.parametrize("listed,want", [
    ("C:\\Lake\\Tables\\dbo\\events\\part-1.parquet", "part-1.parquet"),
    ("c:/lake/tables/DBO/events/part-1.parquet", "part-1.parquet"),  # Windows paths ignore case
    ("file:///C:/Lake/Tables/dbo/events/region=EU/part%202.parquet", "region=EU/part 2.parquet"),
    ("part-1.parquet", "part-1.parquet"),
    ("deletion_vector_1.bin", "deletion_vector_1.bin"),
])
def test_a_listed_file_is_named_relative_to_the_table_however_delta_rs_spells_it(monkeypatch, listed, want):
    import ntpath
    monkeypatch.setattr(maintenance.os.path, "normcase", ntpath.normcase)
    assert maintenance.relative_to_table("C:\\Lake\\Tables\\dbo\\events", listed) == want


def test_table_files_hold_every_data_file_not_only_parquet(tmp_path):
    (tmp_path / "_delta_log").mkdir()
    (tmp_path / "_delta_log" / "00000000000000000000.json").write_text("{}")
    (tmp_path / "region=EU").mkdir()
    for name in ("a.parquet", "region=EU/b.parquet", "deletion_vector_x.bin"):
        (tmp_path / name).write_bytes(b"x")
    assert maintenance.table_files(str(tmp_path)) == {"a.parquet", "region=EU/b.parquet", "deletion_vector_x.bin"}

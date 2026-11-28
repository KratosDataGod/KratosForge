"""PB-067: a first load that stopped part way (out of memory, the app closed) before any write committed to
its table leaves a folder no Delta table opens. Table health reports its files, and Compact removes them at
the ages it removes a table's: stale staged files at once, data files once as old as the history kept.
Never a file in a linked folder, never one without Delta's names, never in a folder that holds a commit."""

import os
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest
from deltalake.exceptions import TableNotFoundError
from test_cloud import svc  # noqa: F401 - the fixture

from fabric_desktop import maintenance

WEEK = 8 * 86400  # older than the 7 days of history kept


def _part() -> str:
    return f"part-00000-{uuid.uuid4()}-c000.snappy.parquet"


def _plant(root: Path, name: str, age: float = 0) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 10)
    then = time.time() - age
    os.utime(path, (then, then))
    return path


def _folder(root: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    """A table folder no write committed to: what Compact removes, and what it keeps."""
    gone = {
        "old_data": _plant(root, _part(), WEEK),
        "old_partition": _plant(root, f"region=EU/{_part()}", WEEK),
        "stale_staged": _plant(root, _part() + "#1", 2 * 3600),
        "stale_staged_commit": _plant(root, "_delta_log/00000000000000000000.json#1", 2 * 3600),
    }
    kept = {
        "fresh_data": _plant(root, _part(), 60),  # a first write may still be under way
        "fresh_staged": _plant(root, _part() + "#2", 60),
        "not_a_delta_name": _plant(root, "notes.parquet", WEEK),
        "a_dropped_csv": _plant(root, "export.csv", WEEK),
        "kept_out_folder": _plant(root, f"_change_data/{_part()}", WEEK),
    }
    return gone, kept


def test_compact_removes_what_a_first_write_that_stopped_left_and_keeps_the_rest(tmp_path):
    root = tmp_path / "t"
    gone, kept = _folder(root)
    elsewhere = tmp_path / "elsewhere"
    outside = _plant(elsewhere, _part(), WEEK)
    (root / "linked").symlink_to(elsewhere, target_is_directory=True)

    found = maintenance.uncommitted_leftovers(str(root), maintenance.retention_hours(7))
    assert found == sorted(p.relative_to(root).as_posix() for p in gone.values())
    assert all(p.exists() for p in gone.values())  # listing deletes nothing

    done = maintenance.compact_local(str(root))
    assert done["unnamed_removed"] == 2 and done["staged_removed"] == 2
    assert done["files_before"] == done["files_after"] == done["vacuumed"] == 0
    assert not any(p.exists() for p in gone.values())
    assert all(p.exists() for p in kept.values()) and outside.exists()



def test_a_shorter_history_never_shortens_how_long_a_first_writes_data_files_are_kept(tmp_path):
    """A Spark or notebook first write takes no compaction_lock and commits its part files only at its end:
    with allow_shorter and 0 days, a data file younger than MIN_RETENTION_DAYS is still kept."""
    root = tmp_path / "t"
    day_old = _plant(root, _part(), 86400)
    week_old = _plant(root, _part(), WEEK)
    staged = _plant(root, _part() + "#1", 2 * 3600)

    done = maintenance.compact_local(str(root), days=0, allow_shorter=True)
    assert done["unnamed_removed"] == 1 and done["staged_removed"] == 1
    assert day_old.exists() and not week_old.exists() and not staged.exists()


def test_a_commit_that_lands_after_the_listing_stops_the_deletes(tmp_path, monkeypatch):
    """A writer that commits between Compact's listing and its delete makes the folder a table: nothing more goes."""
    root = tmp_path / "t"
    gone, _kept = _folder(root)
    listed = maintenance.uncommitted_leftovers

    def list_then_commit(uri, hours):
        found = listed(uri, hours)
        _plant(root, "_delta_log/00000000000000000000.json")
        return found

    monkeypatch.setattr(maintenance, "uncommitted_leftovers", list_then_commit)
    done = maintenance.compact_local(str(root))
    assert done["unnamed_removed"] == done["staged_removed"] == 0
    assert all(p.exists() for p in gone.values())

def test_health_reports_the_files_on_this_pc_only(tmp_path):
    root = tmp_path / "t"
    gone, _ = _folder(root)
    h = maintenance.health(str(root), local=True)
    # Reported from an hour old, as a table's leftovers are: the stale staged files and the old data.
    assert h["exists"] is False and h["status"] == "missing" and h["leftover_files"] == len(gone)
    assert h["message"].startswith(f"Not written yet: {len(gone)} files (") and "Compact removes" in h["message"]
    assert maintenance.health(str(root)) == {"exists": False, "advice": [], "status": "missing"}
    empty = tmp_path / "empty"
    empty.mkdir()
    assert maintenance.health(str(empty), local=True) == {"exists": False, "advice": [], "status": "missing"}


@pytest.mark.parametrize("log", ["00000000000000000000.json", "00000000000000000003.checkpoint.parquet"])
def test_a_folder_whose_log_holds_a_commit_is_never_cleaned_as_uncommitted(tmp_path, log):
    root = tmp_path / "t"
    old = _plant(root, _part(), WEEK)
    _plant(root, f"_delta_log/{log}", WEEK)
    assert maintenance.uncommitted_leftovers(str(root), 1) is None
    with pytest.raises(Exception):  # noqa: B017 - whatever delta-rs makes of it, nothing is removed
        maintenance.compact_local(str(root))
    assert old.exists()


def test_a_linked_table_folder_or_log_is_never_looked_at(tmp_path):
    real = tmp_path / "real"
    old = _plant(real, _part(), WEEK)
    staged = _plant(real, "_delta_log/00000000000000000000.json#1", 2 * 3600)
    (tmp_path / "t").symlink_to(real, target_is_directory=True)
    assert maintenance.uncommitted_leftovers(str(tmp_path / "t"), 1) is None
    with pytest.raises(TableNotFoundError):
        maintenance.compact_local(str(tmp_path / "t"))

    other = tmp_path / "other"
    other_old = _plant(other, _part(), WEEK)
    (other / "_delta_log").symlink_to(real / "_delta_log", target_is_directory=True)
    assert maintenance.uncommitted_leftovers(str(other), 1) is None
    with pytest.raises(TableNotFoundError):
        maintenance.compact_local(str(other))
    assert old.exists() and staged.exists() and other_old.exists()


def test_no_folder_is_still_an_error(tmp_path):
    assert maintenance.uncommitted_leftovers(str(tmp_path / "none"), 1) is None
    with pytest.raises(TableNotFoundError):
        maintenance.compact_local(str(tmp_path / "none"))
    assert not (tmp_path / "none").exists()


def test_a_first_write_killed_mid_stream_is_cleaned_up_by_compact(tmp_path):
    """A real delta-rs first write ended by its process exiting part way, as running out of memory ends it."""
    root = tmp_path / "t"
    script = textwrap.dedent(f"""
        import os
        import pyarrow as pa
        from deltalake import write_deltalake
        schema = pa.schema([("id", pa.int64())])
        def batches():
            for i in range(6):
                yield pa.record_batch([pa.array(range(i * 500_000, (i + 1) * 500_000))], schema=schema)
            os._exit(3)
        write_deltalake({str(root)!r}, pa.RecordBatchReader.from_batches(schema, batches()), mode="error",
                        target_file_size=1_000_000)
    """)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=300)
    assert done.returncode == 3, done.stderr
    written = [p for p in root.rglob("*") if p.is_file()]
    assert written, "delta-rs wrote no file before it was stopped"
    assert maintenance.compact_local(str(root))["unnamed_removed"] == 0  # fresh: a write may be under way
    assert all(p.exists() for p in written)
    for p in written:
        os.utime(p, (time.time() - WEEK, time.time() - WEEK))
    out = maintenance.compact_local(str(root))
    assert out["unnamed_removed"] + out["staged_removed"] == len(written)
    assert not [p for p in root.rglob("*") if p.is_file()]


def test_table_health_and_compact_all_on_this_pc(svc):  # noqa: F811 - the fixture
    project = svc._project()
    e = project.entities["gold.fact_sales"]
    store = svc._store(project, "dev")
    root = Path(store.uri(project.layer(e.layer).lakehouse, e.schema, e.table))
    for p in sorted(root.rglob("*"), reverse=True):  # as if its first load had stopped part way
        p.unlink() if p.is_file() or p.is_symlink() else p.rmdir()
    gone, kept = _folder(root)

    row = next(t for t in svc.meta_health("dev")["tables"] if t["entity"] == "gold.fact_sales")
    assert row["exists"] is False and row["leftover_files"] == len(gone) and row["message"].startswith("Not written yet")

    done = {d["table"]: d for d in svc.meta_optimize("dev")}
    assert done["dbo.fact_sales"]["status"] == "Completed", done["dbo.fact_sales"]
    assert done["dbo.fact_sales"]["unnamed_removed"] == 2 and done["dbo.fact_sales"]["staged_removed"] == 2
    assert not any(p.exists() for p in gone.values()) and all(p.exists() for p in kept.values())
    assert all(d["status"] == "Completed" for t, d in done.items() if not t.startswith("meta.")), done

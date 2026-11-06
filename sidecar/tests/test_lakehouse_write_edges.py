"""Notebook writes and reads through the attached lakehouse (lakehouse.py): the edges the main tests leave
out. A failed stream leaves nothing behind, refused writes write nothing, DELETE/UPDATE record what they
did, and time travel points are checked before anything is read.

Local only: tables live under tmp_path, OneLake's file store is stood in for, no network."""

import datetime as dt
import json
import threading
from concurrent.futures import Future

import duckdb
import pandas as pd
import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable
from deltalake.exceptions import TableNotFoundError

from fabric_desktop import delta_dml, lakehouse, onelake
from fabric_desktop import publish as publish_mod


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path / "work",
                                        history_file=tmp_path / "history.jsonl"))
    yield root
    lakehouse.attach(None)


def _history(tmp_path):
    return [json.loads(line) for line in (tmp_path / "history.jsonl").read_text().splitlines()]


# --------------------------------------------------------------------------- what a write says

def test_a_write_result_says_new_table_unchanged_and_lease_warnings():
    new = lakehouse.WriteResult("dbo.t", 3, 0, "uri")
    assert "Wrote 3 rows to dbo.t (Delta version 0)" in repr(new) and "refresh the lakehouse" in repr(new)
    later = lakehouse.WriteResult("dbo.t", 3, 4, "uri", warning="the write lease lapsed")
    assert "refresh" not in repr(later) and repr(later).endswith("\nWarning: the write lease lapsed")
    same = lakehouse.WriteResult("dbo.t", 3, 4, "uri", skipped=True)
    assert repr(same).startswith("dbo.t unchanged") and "nothing was uploaded" in repr(same)


def test_a_pending_write_waits_for_its_upload_and_never_prints_a_failed_one():
    done = Future()
    pending = lakehouse.PendingWrite("dbo.t", 2, "uri", done)
    done.set_result(lakehouse.WriteResult("dbo.t", 2, 5, "uri", skipped=True))
    assert pending.delta_version == 5 and pending.skipped and "unchanged" in repr(pending)
    failed = Future()
    failed.set_exception(OSError("upload dropped"))
    assert repr(lakehouse.PendingWrite("dbo.t", 2, "uri", failed)) == "dbo.t: not written (the cell's error says why)"
    cancelled = Future()
    cancelled.cancel()
    assert "not written" in repr(lakehouse.PendingWrite("dbo.t", 2, "uri", cancelled))


# --------------------------------------------------------------------------- what a write is given

def test_every_kind_of_table_a_cell_holds_is_written(lake):
    con = duckdb.connect()
    for i, data in enumerate([
        pl.LazyFrame({"id": [1, 2]}),
        pd.DataFrame({"id": [1, 2]}, index=[10, 11]),  # the index isn't a column
        con.sql("SELECT * FROM range(2) t(id)"),
        {"id": [1, 2]},
    ]):
        out = lakehouse.write_table(data, f"dbo.t{i}")
        assert out.rows == 2 and DeltaTable(str(lake / "dbo" / f"t{i}")).to_pyarrow_table().column_names == ["id"]


def test_a_stream_of_data_frames_is_one_commit(lake):
    def parts():
        yield pl.DataFrame({"id": [1, 2]})
        yield pa.record_batch({"id": pa.array([3], pa.int32())})  # a narrower type: cast to the first part's
        yield pl.DataFrame({"id": [4]})
    out = lakehouse.write_table(parts(), "dbo.stream")
    table = DeltaTable(str(lake / "dbo" / "stream"))
    assert out.rows == 4 and table.version() == 0
    assert sorted(table.to_pyarrow_table().column("id").to_pylist()) == [1, 2, 3, 4]


def test_an_empty_stream_is_refused_before_anything_is_staged(lake, tmp_path):
    with pytest.raises(ValueError, match="empty stream"):
        lakehouse.write_table(iter([]), "dbo.none")
    assert not (lake / "dbo" / "none").exists()


def test_a_stream_that_fails_part_way_writes_nothing_and_leaves_no_spill(lake, tmp_path):
    def parts():
        yield pl.DataFrame({"id": [1, 2]})
        raise ConnectionError("the source database went away")
    with pytest.raises(ConnectionError):
        lakehouse.write_table(parts(), "dbo.half")
    assert not (lake / "dbo" / "half").exists()
    spills = tmp_path / "work" / "staging" / "_writes"
    assert not spills.exists() or list(spills.iterdir()) == []


@pytest.mark.parametrize("kw, message", [({"mode": "upsert"}, "mode must be one of"),
                                         ({"upload": "later"}, "upload must be one of")])
def test_a_wrong_mode_or_upload_is_refused(lake, kw, message):
    with pytest.raises(ValueError, match=message):
        lakehouse.write_table(pl.DataFrame({"id": [1]}), "dbo.t", **kw)
    assert not (lake / "dbo" / "t").exists()


def test_a_write_the_cell_check_refuses_writes_nothing(lake):
    lakehouse.cell_write_checks[threading.current_thread()] = lambda name, schema, table: (
        f"{schema}.{table} is read-only in this notebook" if table == "locked" else None)
    try:
        with pytest.raises(PermissionError, match="dbo.locked is read-only"):
            lakehouse.write_table(pl.DataFrame({"id": [1]}), "dbo.locked")
        with pytest.raises(PermissionError, match="read-only"):
            lakehouse.change_table(delta_dml.parse("DELETE FROM dbo.locked WHERE id = 1"))
        assert not (lake / "dbo" / "locked").exists()
        lakehouse.write_table(pl.DataFrame({"id": [1]}), "dbo.open")
    finally:
        lakehouse.cell_write_checks.pop(threading.current_thread(), None)


def test_a_timestamp_without_a_zone_is_refused_by_name(lake):
    naive = pl.DataFrame({"id": [1], "at": [dt.datetime(2026, 1, 1, 9, 0)]})
    with pytest.raises(ValueError, match="Column at holds timestamps without a time zone"):
        lakehouse.write_table(naive, "dbo.events")
    assert not (lake / "dbo" / "events").exists()


def test_a_broken_settings_file_keeps_the_default(monkeypatch):
    def broken(key):
        raise json.JSONDecodeError("Expecting value", "", 0)
    monkeypatch.setattr("fabric_desktop.app_settings.value", broken)
    assert lakehouse._setting("onelake_skip_unchanged", True) is True


# --------------------------------------------------------------------------- DELETE and UPDATE

def test_a_delete_and_update_record_their_rows_and_version_in_the_history(lake, tmp_path):
    lakehouse.write_table(pl.DataFrame({"id": [1, 2, 3], "qty": [5, 6, 7]}), "dbo.orders")
    gone = lakehouse.change_table(delta_dml.parse("DELETE FROM dbo.orders WHERE id = 1"))
    assert (gone.rows_deleted, gone.delta_version) == (1, 1)
    assert repr(gone) == "DELETE into dbo.orders: 0 inserted, 0 updated, 1 deleted (Delta version 1)"
    changed = lakehouse.change_table(delta_dml.parse("UPDATE dbo.orders SET qty = 0 WHERE id > 1"))
    assert changed.rows_updated == 2
    history = _history(tmp_path)
    assert [(h["mode"], h["rows"], h["delta_version"]) for h in history[-2:]] == [("delete", 1, 1), ("update", 2, 2)]
    assert history[-1]["job"] == "Notebook UPDATE in Sales"
    assert sorted(DeltaTable(str(lake / "dbo" / "orders")).to_pyarrow_table().column("qty").to_pylist()) == [0, 0]


def test_changing_a_table_that_isnt_there_says_to_create_it(lake):
    with pytest.raises(TableNotFoundError, match="dbo.missing isn't in Sales.*Create it first"):
        lakehouse.change_table(delta_dml.parse("DELETE FROM dbo.missing WHERE id = 1"))
    assert not (lake / "dbo" / "missing").exists()


# --------------------------------------------------------------------------- time travel points

@pytest.mark.parametrize("version, want", [(3, 3), ("v4", 4), (" V0 ", 0)])
def test_a_version_point_takes_spark_spellings(version, want):
    assert lakehouse.as_of(version) == want


@pytest.mark.parametrize("kw, message", [
    ({"version": "latest"}, "versionAsOf must be a whole number"),
    ({"version": -1}, "can't be negative"),
    ({"timestamp": "yesterday"}, "timestampAsOf must be a date"),
    ({"version": 1, "timestamp": "2026-01-01"}, "not both"),
])
def test_a_wrong_time_travel_point_is_refused(kw, message):
    with pytest.raises(ValueError, match=message):
        lakehouse.as_of(**kw)


def test_a_time_without_a_zone_is_utc_and_a_zoned_one_is_kept():
    assert lakehouse.as_of(timestamp="2026-10-20 13:00:00") == dt.datetime(2026, 10, 20, 13, tzinfo=dt.timezone.utc)
    assert lakehouse.as_of(timestamp="2026-10-20T13:00:00Z").tzinfo == dt.timezone.utc
    zoned = dt.datetime(2026, 10, 20, 13, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert lakehouse.as_of(timestamp=zoned) is zoned
    assert lakehouse.as_of(timestamp=dt.datetime(2026, 10, 20)).tzinfo == dt.timezone.utc
    assert lakehouse.as_of() is None


# --------------------------------------------------------------------------- Files/

class FileStore:
    def __init__(self, token):
        self.token = token

    def list(self, workspace, lakehouse_id, directory):
        return [{"path": f"{directory}/a.parquet"}, {"path": f"{directory}/b.csv"}]


def test_files_lists_the_lakehouse_folder(lake, monkeypatch):
    monkeypatch.setattr(lakehouse, "FileStore", FileStore)
    assert lakehouse.files("Files/raw") == ["Files/raw/a.parquet", "Files/raw/b.csv"]


def test_a_table_path_is_not_a_file(lake):
    with pytest.raises(ValueError, match="not a file under Files/. Read tables with read_table"):
        lakehouse.local_path("Tables/dbo/orders")


def test_a_path_on_this_pc_is_read_where_it_is(tmp_path):
    path = tmp_path / "local.parquet"
    pl.DataFrame({"id": [1, 2]}).write_parquet(path)
    assert lakehouse.read_parquet(str(path))["id"].to_list() == [1, 2]


def test_a_read_record_that_fails_never_fails_the_read(monkeypatch):
    lakehouse.reads_reset()
    monkeypatch.setattr(lakehouse, "proof_exempt_thread", None)

    def boom():
        raise OSError("the file went")
    lakehouse._record("files", "lh1/files/a.csv", boom)
    assert lakehouse.reads["files"] == {}
    lakehouse._record("files", "lh1/files/a.csv", lambda: {"path": "Files/a.csv"})
    assert lakehouse.reads["files"] == {"lh1/files/a.csv": {"path": "Files/a.csv"}}
    lakehouse.reads_reset()


def test_an_all_null_column_appended_to_a_new_table_is_refused_by_name(lake):
    """With a table to append to, the column takes its type; a new table has none to give."""
    data = pl.DataFrame({"id": [1], "note": [None]}, schema={"id": pl.Int64, "note": pl.Null})
    with pytest.raises(ValueError, match="Column note is NULL in every row"):
        lakehouse.write_table(data, "dbo.fresh", mode="append")
    assert not (lake / "dbo" / "fresh").exists()
    lakehouse.write_table(pl.DataFrame({"id": [1], "note": ["a"]}), "dbo.fresh")
    assert lakehouse.write_table(data, "dbo.fresh", mode="append").delta_version == 1


def test_sql_reads_the_cells_own_writes(lake):
    lakehouse.write_table(pl.DataFrame({"id": [1, 2, 3]}), "dbo.nums")
    assert lakehouse.sql("SELECT count(*) AS n FROM dbo.nums")["n"].to_list() == [3]

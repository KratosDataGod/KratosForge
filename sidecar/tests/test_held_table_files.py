"""A table's file that a virus scanner, the search indexer or OneDrive holds for a moment on Windows.

delta-rs reports Windows refusing a file another program has open as an OSError whose text ends
"(os error 32)" (a sharing violation) or "(os error 33)" (a lock violation). A Delta log file was already
waited out (retrying.windows_log_race); this covers a lock violation on the log too, a data file VACUUM
deletes (the app's Compact and a notebook's VACUUM), and a local file a notebook reads to copy or upload.
Each is waited out for about 1.5 s; still held, the error says so plainly and the table is unchanged.
The fakes raise delta-rs's own Windows wording, since a Linux runner can't hold a file open that way.
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import jsonfile, lakehouse, maintenance, retrying
from tests.test_spark_writer import lake  # noqa: F401 - a fixture

HELD = ("Generic LocalFileSystem error: Unable to delete file D:\\a\\t\\part-00001-6f1c.snappy.parquet: "
        "The process cannot access the file because it is being used by another process. (os error 32)")
LOCKED_LOG = ("Generic LocalFileSystem error: Unable to open file D:\\a\\t\\_delta_log\\00000000000000000003.json: "
              "The process cannot access the file because another process has locked a portion of the file. (os error 33)")


def test_which_refusals_count_as_a_file_another_program_holds():
    assert retrying.windows_file_held(OSError(HELD))
    assert retrying.windows_file_held(OSError(LOCKED_LOG))
    assert retrying.windows_log_race(OSError(LOCKED_LOG))  # a lock violation on the log is the log race too
    assert not retrying.windows_log_race(OSError(HELD))  # a data file isn't the log
    for other in ("No space left on device (os error 28)", "Access is denied. (os error 5)",
                  "Generic LocalFileSystem error: Unable to delete file x (os error 321)"):
        assert not retrying.windows_file_held(OSError(other)), other
    assert not retrying.windows_file_held(ValueError(HELD))  # only what delta-rs raises as an OSError


def _held_vacuum(monkeypatch, times):
    """DeltaTable.vacuum refused ``times`` times, as while a scanner holds a file it deletes, then the real one."""
    real = DeltaTable.vacuum
    calls = []

    def vacuum(self, *a, **kw):
        if not kw.get("dry_run", True):
            calls.append(kw)
            if len(calls) <= times:
                raise OSError(HELD)
        return real(self, *a, **kw)
    monkeypatch.setattr(DeltaTable, "vacuum", vacuum)
    monkeypatch.setattr(retrying.time, "sleep", lambda s: None)
    return calls


def _table(tmp_path):
    uri = str(tmp_path / "sales")
    for i in range(3):
        write_deltalake(uri, pa.table({"id": [i]}), mode="append")
    return uri


def _ids(uri):
    return sorted(DeltaTable(uri).to_pyarrow_table()["id"].to_pylist())


def test_compact_waits_out_a_file_held_for_a_moment(tmp_path, monkeypatch):
    uri = _table(tmp_path)
    calls = _held_vacuum(monkeypatch, 2)
    out = maintenance.compact_local(uri)
    assert len(calls) == 3 and out["files_after"] == 1
    assert _ids(uri) == [0, 1, 2]


def test_compact_still_held_says_so_and_keeps_the_rows(tmp_path, monkeypatch):
    uri = _table(tmp_path)
    calls = _held_vacuum(monkeypatch, 99)
    with pytest.raises(OSError) as caught:
        maintenance.compact_local(uri)
    assert str(caught.value) == ("Compact stopped because another program has one of the table's files open, such as a "
                                 "virus scanner, the search indexer or OneDrive. The table's rows are unchanged. "
                                 "Try again in a moment.")
    assert "os error 32" in str(caught.value.__cause__)  # delta-rs's own words kept for the log
    assert len(calls) == 6  # about 1.5 s, then the error
    assert _ids(uri) == [0, 1, 2]


def test_compact_raises_any_other_vacuum_failure_as_it_was(tmp_path, monkeypatch):
    uri = _table(tmp_path)
    monkeypatch.setattr(retrying.time, "sleep", lambda s: None)
    real = DeltaTable.vacuum
    full = OSError("No space left on device (os error 28)")

    def vacuum(self, *a, **kw):
        if not kw.get("dry_run", True):
            raise full
        return real(self, *a, **kw)
    monkeypatch.setattr(DeltaTable, "vacuum", vacuum)
    with pytest.raises(OSError) as caught:
        maintenance.compact_local(uri)
    assert caught.value is full


@pytest.fixture
def guarded(lake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, table: None)
    return lake


def test_a_notebook_vacuum_waits_out_a_held_file_then_says_so(guarded, monkeypatch):
    uri = str(guarded / "dbo" / "sales")
    before = DeltaTable(uri).version()
    rows = _ids(uri) if "id" in DeltaTable(uri).schema().to_arrow().names else None
    calls = _held_vacuum(monkeypatch, 1)
    done = lakehouse.maintain_table("dbo.sales", "vacuum")
    assert len(calls) == 2 and done.metrics["dryRun"] is False
    calls = _held_vacuum(monkeypatch, 99)
    with pytest.raises(OSError, match=r"^VACUUM of dbo\.sales stopped because another program has one of the "
                                      r"table's files open.*The table's rows are unchanged\."):
        lakehouse.maintain_table("dbo.sales", "vacuum")
    assert len(calls) == 6
    if rows is not None:
        assert _ids(uri) == rows
    assert DeltaTable(uri).version() >= before


def test_a_local_file_held_for_a_moment_is_read_once_its_free(tmp_path, monkeypatch):
    path = tmp_path / "sales.csv"
    path.write_bytes(b"id\n1\n")
    real_open, tries = open, []

    def opening(p, mode="r", *a, **kw):
        if str(p) == str(path):
            tries.append(p)
            if len(tries) <= 2:
                exc = PermissionError(13, "The process cannot access the file because it is being used by another process")
                exc.winerror = 32
                raise exc
        return real_open(p, mode, *a, **kw)
    monkeypatch.setattr("builtins.open", opening)
    monkeypatch.setattr(jsonfile.time, "sleep", lambda s: None)
    assert jsonfile.read_bytes(path) == b"id\n1\n"
    tries.clear()
    assert jsonfile.read_bytes(path, limit=2) == b"id"


def test_a_local_file_still_held_names_the_file(tmp_path, monkeypatch):
    from fabric_desktop import file_locks

    path = tmp_path / "sales.csv"
    path.write_bytes(b"id\n1\n")
    real_open = open

    def opening(p, mode="r", *a, **kw):
        if str(p) == str(path):
            exc = PermissionError(13, "The process cannot access the file because it is being used by another process")
            exc.winerror = 32
            raise exc
        return real_open(p, mode, *a, **kw)
    monkeypatch.setattr("builtins.open", opening)
    monkeypatch.setattr(jsonfile.time, "sleep", lambda s: None)
    monkeypatch.setattr(file_locks, "holders", lambda p: ["Microsoft Excel"])
    with pytest.raises(PermissionError) as caught:
        jsonfile.read_bytes(path)
    assert str(caught.value) == "sales.csv couldn't be read because it is open in Microsoft Excel. Close it there and try again."

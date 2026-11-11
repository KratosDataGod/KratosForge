"""A local data project's engine (localdata/engine.py): more of the paths the main tests don't reach. Delta
tables a local read can't apply (a newer reader, partitions outside their folders, a version that fails to
load), an Excel file, a database another program holds, and a reader that fails as it starts. A stand-in
Delta table where delta-rs can't make the shape; the rest is on local disk."""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest

from fabric_desktop.localdata import engine as eng
from fabric_desktop.localdata.discovery import Found
from tests.test_local_data_engine_edges import engine, local  # noqa: F401 - fixtures


class Table:
    """The parts of a deltalake DeltaTable that delta_select and _load read."""

    def __init__(self, reader=1, files=(), partitions=(), fail_load=None, history=()):
        self.reader, self.files, self.partitions = reader, list(files), list(partitions)
        self.fail_load, self._history, self.loaded = fail_load, list(history), []

    def protocol(self):
        return SimpleNamespace(reader_features=None, writer_features=None, min_reader_version=self.reader,
                               min_writer_version=2)

    def metadata(self):
        return SimpleNamespace(configuration={}, partition_columns=self.partitions)

    def version(self):
        return 4

    def file_uris(self):
        return self.files

    def schema(self):
        return SimpleNamespace(to_arrow=lambda: pa.schema([("id", pa.int64()), ("day", pa.string())]))

    def load_as_version(self, v):
        if self.fail_load:
            raise self.fail_load
        self.loaded.append(v)

    def history(self):
        return self._history


@pytest.fixture
def table(monkeypatch):
    def use(t):
        import deltalake
        monkeypatch.setattr(deltalake, "DeltaTable", lambda folder: t)
        return t
    return use


def test_a_delta_table_needing_a_newer_reader_isnt_queryable_here(table):
    table(Table(reader=4))
    with pytest.raises(ValueError, match="uses reader version 4, which a local read can't apply yet"):
        eng.delta_select("/lake/t")


def test_partition_columns_outside_their_folders_cant_be_read_locally(table, monkeypatch):
    monkeypatch.setattr(eng, "_arrow_schema", lambda t: pa.schema([("id", pa.int64()), ("day", pa.string())]))
    monkeypatch.setattr(eng, "_size", lambda f: 1)
    table(Table(files=["/lake/t/day=1/a.parquet", "/lake/t/b.parquet"], partitions=["day"]))
    with pytest.raises(ValueError, match=r"files aren't in day=\.\.\. folders, so its partition column day"):
        eng.delta_select("/lake/t")


def test_a_version_that_fails_to_load_says_delta_rs_s_own_words(table):
    table(Table(fail_load=OSError("checkpoint file missing\nmore detail")))
    with pytest.raises(ValueError) as raised:
        eng._load("/lake/t", 2, None, "dbo.sales")
    assert str(raised.value).startswith("dbo.sales can't be read at version 2: checkpoint file missing")


def test_a_time_that_fails_to_load_is_the_same_plain_message(table):
    table(Table(fail_load=RuntimeError("no commit at or before that time"), history=[{"timestamp": 1}]))
    with pytest.raises(ValueError, match=r"^/lake/t has no version as of 2026-11-01T00:00:00Z \(UTC\): no commit"):
        eng._load("/lake/t", None, "2026-11-01T00:00:00Z", "")


def test_an_excel_workbook_is_found_but_not_read_yet():
    found = Found(schema="dbo", table="budget", format="excel", paths=["/files/budget.xlsx"])
    with pytest.raises(ValueError, match="Excel workbooks are found but can't be read yet"):
        eng.file_select(found)


def test_a_database_another_program_holds_says_to_close_it_there():
    busy = eng._friendly_open_error(duckdb_error("IO Error: Could not set lock on file"), "C:/data/p.duckdb")
    assert isinstance(busy, eng.EngineBusy)
    assert str(busy).startswith("C:/data/p.duckdb is open in another program (another KratosForge window or a notebook)")
    other = duckdb_error("IO Error: permission denied")
    assert eng._friendly_open_error(other, "p") is other


def duckdb_error(text):
    import duckdb
    return duckdb.IOException(text)


def test_the_connection_opens_when_it_is_first_used_and_says_so(engine):  # noqa: F811
    engine.close()
    assert engine.is_open is False
    assert engine.con.execute("SELECT 41 + 1").fetchone() == (42,)
    assert engine.is_open is True


def test_a_reader_that_fails_as_it_starts_frees_the_connection_for_the_next_query(engine, monkeypatch):  # noqa: F811
    def broken(result, rows):
        raise MemoryError("no room for the first batch")

    monkeypatch.setattr(eng, "arrow_reader", broken)
    with pytest.raises(MemoryError, match="no room"):
        engine.execute_reader("SELECT 1 AS n")
    monkeypatch.undo()
    assert engine.execute("SELECT 2 AS n")["rows"] == [[2]]

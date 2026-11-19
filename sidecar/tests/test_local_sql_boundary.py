"""The local SQL engine at its exact edges (boundary re-score of batch 7, mutation batch 158,
docs/quality/mutation-testing.md)."""

from __future__ import annotations

import pyarrow as pa
import pytest

from fabric_desktop.engines import local_sql
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.fixture
def duck():
    session = local_sql.DuckDBSession(SessionConfig("nb"))
    yield session
    session.close()


def test_keep_takes_a_table_of_exactly_the_byte_limit(duck, monkeypatch):
    table = pa.table({"a": [1, 2, 3]})
    monkeypatch.setattr(local_sql, "RESULT_BYTES", table.nbytes)
    kept, total = duck._keep(table, 3, CellRequest("", "sql", result_rows=5, preview_rows=2))
    assert kept.num_rows == 2 and total == 3 and duck._result is table


def _reader(*sizes: int) -> pa.RecordBatchReader:
    schema = pa.schema([("a", pa.int64())])
    start, batches = 0, []
    for n in sizes:
        batches.append(pa.record_batch([pa.array(range(start, start + n), pa.int64())], schema=schema))
        start += n
    return pa.RecordBatchReader.from_batches(schema, batches)


def test_drain_without_a_row_limit_never_checks_the_byte_limit(monkeypatch):
    monkeypatch.setattr(local_sql, "RESULT_BYTES", 1)
    table, total = local_sql._drain(_reader(3), 3, 0)
    assert table.num_rows == 3 and total == 3


def test_drain_takes_rows_of_exactly_the_byte_limit(monkeypatch):
    monkeypatch.setattr(local_sql, "RESULT_BYTES", _reader(2).read_all().nbytes)
    table, total = local_sql._drain(_reader(2), 0, 2)
    assert table.num_rows == 2 and total == 2


def test_drain_refuses_rows_one_byte_over_the_limit(monkeypatch):
    monkeypatch.setattr(local_sql, "RESULT_BYTES", _reader(2).read_all().nbytes - 1)
    with pytest.raises(local_sql.ResultTooLarge):
        local_sql._drain(_reader(2), 0, 2)


@pytest.fixture
def lite():
    session = local_sql.SQLiteSession(SessionConfig("nb"))
    yield session
    session.close()


def test_sqlite_without_a_row_limit_never_checks_the_byte_limit(lite, monkeypatch):
    monkeypatch.setattr(local_sql, "RESULT_BYTES", 1)
    table, total = lite._run(CellRequest("SELECT 1 AS a UNION ALL SELECT 2", "sql", preview_rows=3))
    assert table.num_rows == 2 and total == 2


def test_sqlite_takes_rows_of_exactly_the_byte_limit(lite, monkeypatch):
    size = local_sql._sqlite_table(["a"], [(1,), (2,)]).nbytes
    monkeypatch.setattr(local_sql, "RESULT_BYTES", size)
    table, total = lite._run(CellRequest("SELECT 1 AS a UNION ALL SELECT 2", "sql", result_rows=2, preview_rows=0))
    assert total == 2

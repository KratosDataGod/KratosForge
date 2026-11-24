"""Snowflake SQL engine: the session paths the HTTP-level tests don't reach (cancel between partitions
and statements, a busy session, results with no columns, streamed whole results, plan and catalog
fallbacks). A fake client stands in for snowflake_remote.Client: no network and no credentials."""
from __future__ import annotations

from typing import Any

import pytest

from fabric_desktop.engines import snowflake_warehouse as sw
from fabric_desktop.engines.contract import CellRequest, SessionConfig, Unsupported
from fabric_desktop.engines.local_sql import ResultTooLarge


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class FakeClient:
    """Answers statements from a queue and partitions from a dict; records what it was sent."""

    def __init__(self, answers: list[dict[str, Any]], partitions: dict[int, dict[str, Any]] | None = None,
                 on_statement=None, on_partition=None):
        self.answers = list(answers)
        self.partitions = partitions or {}
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.read: list[int] = []
        self.on_statement = on_statement
        self.on_partition = on_partition
        self.stopped = None

    def statement(self, sql: str, **kw: Any) -> dict[str, Any]:
        self.sent.append((sql, kw))
        if self.on_statement:
            self.on_statement(len(self.sent))
        return self.answers.pop(0) if self.answers else {}

    def partition(self, handle: str, index: int) -> dict[str, Any]:
        self.read.append(index)
        if self.on_partition:
            self.on_partition(index)
        return self.partitions[index]


def col(name: str, kind: str = "text") -> dict[str, Any]:
    return {"name": name, "type": kind}


def answer(row_type: list[dict[str, Any]], first: list[list[Any]], *more: list[list[Any]],
           num_rows: int | None = None) -> dict[str, Any]:
    parts = [first, *more]
    meta: dict[str, Any] = {"rowType": row_type, "partitionInfo": [{"rowCount": len(p)} for p in parts]}
    if num_rows is not None:
        meta["numRows"] = num_rows
    return {"statementHandle": "h-1", "resultSetMetaData": meta, "data": first}


def session(client: FakeClient, **options: Any) -> sw.SnowflakeSqlSession:
    return sw.SnowflakeSqlSession(SessionConfig("nb", "", options), client, "WH")


def sql(code: str, **kw: Any) -> CellRequest:
    return CellRequest(code, language="sql", **kw)


def test_unreadable_sql_is_a_chain_so_it_is_refused():
    assert sw._flow_chain("SELECT 'not closed") is True


def test_more_partitions_than_the_limit_is_an_error_before_any_read(monkeypatch):
    monkeypatch.setattr(sw, "MAX_PARTITIONS", 2)
    client = FakeClient([answer([col("a")], [["1"]], [["2"]], [["3"]])])
    got = session(client).execute(sql("SELECT 1"))
    assert got.status == "error" and "more than 2 partitions" in got.outputs[0]["evalue"]
    assert client.read == []


def test_cancel_between_partitions_stops_reading_and_ends_cancelled():
    holder: dict[str, sw.SnowflakeSqlSession] = {}
    client = FakeClient([answer([col("a")], [["1"]], [["2"]], [["3"]])],
                        {1: {"data": [["2"]]}, 2: {"data": [["3"]]}},
                        on_partition=lambda i: holder["s"].cancel())
    holder["s"] = s = session(client)
    seen: list[dict[str, Any]] = []
    got = s.execute(sql("SELECT 1", result_rows=10), seen.append)
    assert got.status == "cancelled" and got.outputs[0]["evalue"] == "Cancelled."
    assert client.read == [1] and seen == got.outputs


def test_cancel_between_statements_never_sends_the_next():
    holder: dict[str, sw.SnowflakeSqlSession] = {}
    client = FakeClient([answer([col("a")], [["1"]])] * 2, on_statement=lambda n: holder["s"].cancel())
    holder["s"] = s = session(client)
    got = s.execute(sql("SELECT 1; SELECT 2"))
    assert got.status == "cancelled" and len(client.sent) == 1


def test_a_whole_result_without_a_row_count_is_still_capped_by_rows():
    client = FakeClient([answer([col("a")], [["1"], ["2"]], [["3"], ["4"]])], {1: {"data": [["3"], ["4"]]}})
    got = session(client).execute(sql("SELECT 1", result_rows=3))
    assert got.status == "error" and got.outputs[0]["ename"] == "ResultTooLarge"
    assert "more than the 3 rows" in got.outputs[0]["evalue"]


def test_a_whole_result_without_sizes_is_still_capped_by_bytes(monkeypatch):
    monkeypatch.setattr(sw, "RESULT_BYTES", 10)
    client = FakeClient([answer([col("a")], [["a long enough value"]])])
    got = session(client).execute(sql("SELECT 1", result_rows=5))
    assert got.status == "error" and got.outputs[0]["ename"] == "ResultTooLarge"


def test_too_large_is_an_error_not_cancelled_even_after_a_cancel():
    holder: dict[str, sw.SnowflakeSqlSession] = {}
    client = FakeClient([answer([col("a")], [["1"]], [["2"], ["3"]])], {1: {"data": [["2"], ["3"]]}},
                        on_partition=lambda i: holder["s"].cancel())
    holder["s"] = s = session(client)
    got = s.execute(sql("SELECT 1", result_rows=2))
    assert got.status == "error" and isinstance(got.outputs[0], dict)
    assert got.outputs[0]["ename"] == ResultTooLarge.__name__


def test_a_busy_session_refuses_a_second_cell_without_sending_it():
    client = FakeClient([])
    s = session(client)
    assert s._lock.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            s.execute(sql("SELECT 1"))
    finally:
        s._lock.release()
    assert client.sent == []


def test_a_statement_with_no_columns_reports_done_on_the_warehouse():
    client = FakeClient([{"statementHandle": "h", "resultSetMetaData": {"rowType": []}}])
    seen: list[dict[str, Any]] = []
    got = session(client).execute(sql("SELECT 1"), seen.append)
    assert got.status == "ok" and got.rows is None
    assert got.outputs[0]["type"] == "text" and "on WH" in got.outputs[0]["text"] and seen == got.outputs


def test_a_statement_with_no_columns_needs_no_output_sink():
    client = FakeClient([{"statementHandle": "h", "resultSetMetaData": {}}])
    got = session(client).execute(sql("SHOW TABLES"))
    assert got.status == "ok" and got.outputs[0]["type"] == "text"


def test_a_whole_result_streams_its_outputs_and_names_text_columns():
    client = FakeClient([answer([col("a", "fixed"), col("v", "variant")], [["1", "{}"], ["2", "[]"]], num_rows=2)])
    seen: list[dict[str, Any]] = []
    got = session(client).execute(sql("SELECT 1", result_rows=5, preview_rows=1), seen.append)
    assert got.status == "ok" and got.rows == 2 and got.table.num_rows == 2
    assert seen == got.outputs and got.outputs[0] == {"type": "stream", "text": "Kept as text: v (variant).\n"}


def test_two_columns_of_one_name_in_a_whole_result_are_streamed_as_an_error():
    client = FakeClient([answer([col("a"), col("a")], [["1", "2"]], num_rows=1)])
    seen: list[dict[str, Any]] = []
    got = session(client).execute(sql("SELECT 1", result_rows=5), seen.append)
    assert got.status == "error" and seen == got.outputs and "named a" in got.outputs[0]["evalue"]


def test_plan_takes_one_statement():
    client = FakeClient([])
    with pytest.raises(Unsupported, match="one statement"):
        session(client).plan("SELECT 1; SELECT 2")
    assert client.sent == []


def test_catalog_with_only_a_database_lists_its_schemas_off_the_warehouse():
    client = FakeClient([answer([col("name")], [["PUBLIC"], [None]])])
    got = session(client, database="my db").catalog()
    assert client.sent[0][0] == 'SHOW SCHEMAS IN DATABASE "my db"' and client.sent[0][1]["warehouse"] == ""
    assert got == [{"catalog": "my db", "schema": "PUBLIC", "name": "", "type": "schema"},
                   {"catalog": "my db", "schema": "", "name": "", "type": "schema"}]


def test_catalog_with_nothing_set_lists_databases(monkeypatch):
    monkeypatch.setattr(sw, "CATALOG_LIMIT", 1)
    client = FakeClient([answer([col("name")], [["DB1"], ["DB2"]])])
    got = session(client).catalog()
    assert client.sent[0][0] == "SHOW DATABASES"
    assert got == [{"catalog": "DB1", "schema": "", "name": "", "type": "catalog"}]

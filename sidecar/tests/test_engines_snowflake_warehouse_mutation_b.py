"""Mutation-testing follow-ups for engines/snowflake_warehouse.py (batch 460): the limits a session
sends and keeps, the tokens around a command's tail, a preview cut to its rows, a whole result at
exactly its byte limit, rows a plan or catalog answer can't use, and what cancel and describe return.
A fake client stands in for snowflake_remote.Client: no network and no credentials."""
from __future__ import annotations

import json
from typing import Any

import pytest

from fabric_desktop.engines import snowflake_warehouse as sw
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class FakeClient:
    def __init__(self, answers: list[dict[str, Any]], partitions: dict[int, dict[str, Any]] | None = None):
        self.answers = list(answers)
        self.partitions = partitions or {}
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.read: list[int] = []
        self.stopped = None

    def statement(self, sql: str, **kw: Any) -> dict[str, Any]:
        self.sent.append((sql, kw))
        return self.answers.pop(0) if self.answers else {}

    def partition(self, handle: str, index: int) -> dict[str, Any]:
        self.read.append(index)
        return self.partitions[index]


def col(name: str, kind: str = "text", **more: Any) -> dict[str, Any]:
    return {"name": name, "type": kind, **more}


def answer(row_type, first, *more, num_rows=None, sizes=None) -> dict[str, Any]:
    parts = [first, *more]
    info = [{"rowCount": len(p)} for p in parts]
    for i, s in enumerate(sizes or []):
        info[i]["uncompressedSize"] = s
    meta: dict[str, Any] = {"rowType": row_type, "partitionInfo": info}
    if num_rows is not None:
        meta["numRows"] = num_rows
    return {"statementHandle": "h-1", "resultSetMetaData": meta, "data": first}


def session(client: FakeClient, **options: Any) -> sw.SnowflakeSqlSession:
    return sw.SnowflakeSqlSession(SessionConfig("nb", "", options), client, "WH")


def sql(code: str, **kw: Any) -> CellRequest:
    return CellRequest(code, language="sql", **kw)


# ---- limits


def test_a_statement_may_run_two_hours_and_is_followed_a_minute_longer():
    assert sw.MAX_SECONDS == 7200
    client = FakeClient([answer([col("a")], [["1"]])])
    assert sw.SnowflakeSqlSession(SessionConfig("nb", "", {}), client, "WH").execute(sql("SELECT 1")).status == "ok"
    kw = client.sent[0][1]
    assert kw["timeout"] == 7200 and kw["max_wait"] == 7260


def test_ten_thousand_partitions_are_read_and_one_more_is_refused():
    assert sw.MAX_PARTITIONS == 10_000
    many = answer([col("a")], [["1"]])
    many["resultSetMetaData"]["partitionInfo"] = [{"rowCount": 1}] * 10_000
    client = FakeClient([many])
    got = session(client).execute(sql("SELECT 1", preview_rows=1))
    assert got.status == "ok" and client.read == []
    many["resultSetMetaData"]["partitionInfo"] = [{"rowCount": 1}] * 10_001
    got = session(FakeClient([many])).execute(sql("SELECT 1", preview_rows=1))
    assert got.status == "error" and "more than 10,000 partitions" in got.outputs[0]["evalue"]


def test_exactly_the_partition_limit_is_read(monkeypatch):
    monkeypatch.setattr(sw, "MAX_PARTITIONS", 2)
    client = FakeClient([answer([col("a")], [["1"]], [["2"]])], {1: {"data": [["2"]]}})
    got = session(client).execute(sql("SELECT 1", result_rows=10))
    assert got.status == "ok" and got.table.column("a").to_pylist() == ["1", "2"]


def test_catalog_lists_at_most_a_thousand_entries():
    assert sw.CATALOG_LIMIT == 1000
    names = {"resultSetMetaData": {"rowType": [col("name")]}, "data": [[f"DB{i}"] for i in range(1001)]}
    got = session(FakeClient([names])).catalog()
    assert len(got) == 1000 and got[-1]["catalog"] == "DB999"


# ---- reading the SQL


def test_a_statement_without_a_chain_is_not_one():
    assert sw._flow_chain("SELECT 1") is False


def test_a_command_with_nothing_after_it_is_not_a_chain():
    assert sw._flow_chain("EXPLAIN") is False


def test_a_command_tail_after_other_tokens_is_read_again():
    assert sw._flow_chain("SELECT 1 FROM t EXPLAIN 'a ->> b'") is True


def test_the_first_word_leaves_out_leading_space():
    assert sw._first_word("   merge into t") == "MERGE"


# ---- typing


def test_a_negative_precision_reads_as_the_smallest_decimal():
    table, as_text = sw.to_table([col("d", "fixed", scale=1, precision=-1)], [["0.5"]])
    assert as_text == [] and str(table.schema.field("d").type) == "decimal128(1, 1)"


# ---- results


def test_a_preview_keeps_only_its_rows_of_a_larger_first_partition():
    client = FakeClient([answer([col("a")], [[str(i)] for i in range(5)], num_rows=5)])
    got = session(client).execute(sql("SELECT 1", preview_rows=2))
    assert got.status == "ok" and got.outputs[0]["rows"] == [["0"], ["1"]] and got.rows == 5


def test_a_result_of_one_row_is_a_whole_result():
    got = session(FakeClient([answer([col("a")], [["x"]])])).execute(sql("SELECT 1", result_rows=1))
    assert got.status == "ok" and got.table is not None and got.table.num_rows == 1


def test_a_row_count_that_isnt_a_number_is_ignored():
    got = session(FakeClient([answer([col("a")], [["x"]], num_rows="5")])).execute(sql("SELECT 1", result_rows=3))
    assert got.status == "ok" and got.table.num_rows == 1


def test_an_answer_without_partition_info_keeps_its_rows():
    ans = {"statementHandle": "h", "resultSetMetaData": {"rowType": [col("a")]}, "data": [["1"], ["2"]]}
    got = session(FakeClient([ans])).execute(sql("SELECT 1", result_rows=5))
    assert got.status == "ok" and got.table.column("a").to_pylist() == ["1", "2"]


def test_declared_sizes_over_the_byte_limit_are_refused_before_any_read(monkeypatch):
    monkeypatch.setattr(sw, "RESULT_BYTES", 100)
    client = FakeClient([answer([col("a")], [["1"]], [["2"]], sizes=[60, 41])], {1: {"data": [["2"]]}})
    got = session(client).execute(sql("SELECT 1", result_rows=10))
    assert got.status == "error" and got.outputs[0]["ename"] == "ResultTooLarge" and client.read == []


def test_declared_sizes_at_the_byte_limit_are_read(monkeypatch):
    monkeypatch.setattr(sw, "RESULT_BYTES", 100)
    client = FakeClient([answer([col("a")], [["1"]], [["2"]], sizes=[60, 40])], {1: {"data": [["2"]]}})
    got = session(client).execute(sql("SELECT 1", result_rows=10))
    assert got.status == "ok" and client.read == [1]


def test_rows_of_exactly_the_byte_limit_are_kept_and_one_byte_more_isnt(monkeypatch):
    rows = [["abc"]]
    monkeypatch.setattr(sw, "RESULT_BYTES", len(json.dumps(rows)))
    got = session(FakeClient([answer([col("a")], rows)])).execute(sql("SELECT 1", result_rows=5))
    assert got.status == "ok" and got.table.num_rows == 1
    monkeypatch.setattr(sw, "RESULT_BYTES", len(json.dumps(rows)) - 1)
    got = session(FakeClient([answer([col("a")], rows)])).execute(sql("SELECT 1", result_rows=5))
    assert got.status == "error" and got.outputs[0]["ename"] == "ResultTooLarge"


class _Clock:
    def __init__(self, *ticks: float):
        self.ticks = list(ticks)

    def perf_counter(self) -> float:
        return self.ticks.pop(0)


def test_an_error_reports_the_time_it_took(monkeypatch):
    monkeypatch.setattr(sw, "time", _Clock(1000.0, 1002.5))
    bad = {"statementHandle": "h", "resultSetMetaData": {"rowType": [col("a")]}, "data": [["1", "2"]]}
    got = session(FakeClient([bad])).execute(sql("SELECT 1"))
    assert got.status == "error" and got.seconds == pytest.approx(2.5)


# ---- plan, catalog, cancel, describe


def test_plan_leaves_out_rows_that_arent_lists_or_are_empty():
    ans = {"data": ["junk", [], ["step one"], ["step two"]]}
    assert session(FakeClient([ans])).plan("SELECT 1") == "step one\nstep two"


def test_catalog_objects_keep_the_database_and_schema_snowflake_names():
    found = {"resultSetMetaData": {"rowType": [col("database_name"), col("schema_name"), col("name"), col("kind")]},
             "data": [["OTHER_DB", "OTHER_S", "T", "TABLE"]]}
    got = session(FakeClient([found]), database="DB", schema="S").catalog()
    assert got == [{"catalog": "OTHER_DB", "schema": "OTHER_S", "name": "T", "type": "table"}]


def test_cancel_of_a_running_cell_says_so():
    s = session(FakeClient([]))
    assert s.cancel() is False
    s._lock.acquire()
    try:
        assert s.cancel() is True
    finally:
        s._lock.release()


def test_the_adapter_describes_this_engine():
    assert sw.SnowflakeSqlAdapter(lambda conn: FakeClient([])).describe() is sw.SNOWFLAKE_SQL

"""Mutation batch 302: the Snowflake SQL engine's limits, its read-only check's edges, and how a session
reads partitions, times cells and lists the catalog. A fake client stands in for snowflake_remote.Client:
no network and no credentials."""
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
    """Answers statements from a queue and partitions from a dict; records what it was sent."""

    def __init__(self, answers: list[dict[str, Any]], partitions: dict[int, dict[str, Any]] | None = None,
                 on_statement=None):
        self.answers = list(answers)
        self.partitions = partitions or {}
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.read: list[int] = []
        self.on_statement = on_statement
        self.stopped = None

    def statement(self, sql: str, **kw: Any) -> dict[str, Any]:
        self.sent.append((sql, kw))
        if self.on_statement:
            self.on_statement()
        return self.answers.pop(0) if self.answers else {}

    def partition(self, handle: str, index: int) -> dict[str, Any]:
        self.read.append(index)
        return self.partitions[index]


def col(name: str, kind: str = "text", **kw: Any) -> dict[str, Any]:
    return {"name": name, "type": kind, **kw}


def answer(row_type: list[dict[str, Any]], data: list[list[Any]], parts: list[dict[str, Any]] | None = None,
           **meta: Any) -> dict[str, Any]:
    m: dict[str, Any] = {"rowType": row_type, **meta}
    if parts is not None:
        m["partitionInfo"] = parts
    return {"statementHandle": "h-1", "resultSetMetaData": m, "data": data}


def session(client: FakeClient, **options: Any) -> sw.SnowflakeSqlSession:
    return sw.SnowflakeSqlSession(SessionConfig("nb", "", options), client, "WH")


def sql(code: str, **kw: Any) -> CellRequest:
    return CellRequest(code, language="sql", **kw)


def test_the_limits_are_two_hours_ten_thousand_partitions_and_a_thousand_catalog_rows():
    assert sw.MAX_SECONDS == 7200
    assert sw.MAX_PARTITIONS == 10_000
    assert sw.CATALOG_LIMIT == 1000


def test_a_statement_waits_its_time_limit_and_one_minute_more():
    client = FakeClient([answer([], [])])
    session(client).execute(sql("SELECT 1"))
    kw = client.sent[0][1]
    assert kw["timeout"] == 7200 and kw["max_wait"] == 7260


def test_the_first_word_of_a_statement_is_named_in_capitals_without_its_leading_space():
    assert sw._first_word("  insert into t values (1)") == "INSERT"
    assert sw._first_word("(1)") == "This statement"


def test_a_command_with_nothing_after_it_and_a_command_followed_by_another_statement_arent_chains():
    assert sw._flow_chain("EXPLAIN") is False
    assert sw._flow_chain("EXPLAIN SELECT 1; SELECT 'x->>y'") is False
    assert sw._flow_chain("SELECT 1") is False


def test_exactly_the_partition_limit_is_read_and_one_more_is_refused():
    parts = [{"rowCount": 1}] + [{} for _ in range(9_999)]
    got = session(FakeClient([answer([col("a")], [["1"]], parts)])).execute(sql("SELECT 1", preview_rows=1))
    assert got.status == "ok"
    got = session(FakeClient([answer([col("a")], [["1"]], parts + [{}])])).execute(sql("SELECT 1", preview_rows=1))
    assert got.status == "error" and "more than 10,000 partitions" in got.outputs[0]["evalue"]


def test_a_text_or_yes_no_row_count_is_not_taken_as_snowflakes_count():
    client = FakeClient([answer([col("a")], [["1"], ["2"]], [{"rowCount": 2}], numRows="5")])
    got = session(client).execute(sql("SELECT 1", result_rows=10))
    assert got.status == "ok" and got.table.num_rows == 2


def test_a_whole_result_sized_exactly_at_the_limit_is_kept_and_its_reported_size_is_checked_before_reading(
        monkeypatch):
    rows = [["abc"], ["de"]]
    monkeypatch.setattr(sw, "RESULT_BYTES", len(json.dumps(rows)))
    got = session(FakeClient([answer([col("a")], rows, [{"rowCount": 2}])])).execute(sql("SELECT 1", result_rows=10))
    assert got.status == "ok" and got.table.num_rows == 2
    monkeypatch.setattr(sw, "RESULT_BYTES", 100)
    at = session(FakeClient([answer([col("a")], [["x"]], [{"rowCount": 1, "uncompressedSize": 100}])]))
    assert at.execute(sql("SELECT 1", result_rows=10)).status == "ok"
    client = FakeClient([answer([col("a")], [["x"]], [{"rowCount": 1, "uncompressedSize": 101}, {"rowCount": 1}])],
                        {1: {"data": [["y"]]}})
    got = session(client).execute(sql("SELECT 1", result_rows=10))
    assert got.status == "error" and got.outputs[0]["ename"] == "ResultTooLarge"
    assert client.read == []


def test_a_result_without_partition_info_is_read_from_the_first_answer():
    got = session(FakeClient([answer([col("a")], [["1"], ["2"]])])).execute(sql("SELECT 1", result_rows=10))
    assert got.status == "ok" and got.table.column("a").to_pylist() == ["1", "2"]


def test_a_preview_keeps_only_the_rows_asked_for_from_a_larger_first_partition():
    client = FakeClient([answer([col("a")], [["1"], ["2"], ["3"]], [{"rowCount": 3}])])
    got = session(client).execute(sql("SELECT 1", preview_rows=2))
    assert got.status == "ok" and got.outputs[0]["rows"] == [["1"], ["2"]]


def test_one_whole_row_asked_for_is_a_whole_result():
    got = session(FakeClient([answer([col("a")], [["1"]], [{"rowCount": 1}])])).execute(sql("SELECT 1", result_rows=1))
    assert got.status == "ok" and got.table is not None and got.table.num_rows == 1


def test_an_error_reports_how_long_the_cell_took_not_a_clock_reading():
    client = FakeClient([answer([col("a")], [["1"], ["2"]], [{"rowCount": 2}], numRows=2)])
    got = session(client).execute(sql("SELECT 1", result_rows=1))
    assert got.status == "error"
    assert 0 <= got.seconds < 60


def test_cancel_while_a_cell_runs_answers_yes():
    seen: list[Any] = []
    s = None

    def during():
        seen.append(s.cancel())

    client = FakeClient([answer([], []), answer([], [])], on_statement=during)
    s = session(client)
    got = s.execute(sql("SELECT 1; SELECT 2"))
    assert seen == [True]
    assert got.status == "cancelled"


def test_a_precision_below_one_is_raised_to_one_digit_so_the_column_stays_a_decimal():
    table, as_text = sw.to_table([col("d", "fixed", scale=1, precision=-1)], [["0.5"]])
    assert as_text == [] and str(table.schema.field("d").type) == "decimal128(1, 1)"


def test_plan_skips_rows_that_are_empty_or_arent_rows():
    client = FakeClient([{"data": [["step 1"], [], "noise", ["step 2"]]}])
    assert session(client).plan("SELECT 1") == "step 1\nstep 2"


def test_catalog_keeps_the_database_and_schema_each_row_names():
    client = FakeClient([{"resultSetMetaData": {"rowType": [col("name"), col("database_name"), col("schema_name"),
                                                            col("kind")]},
                          "data": [["T", "OTHER_DB", "OTHER_SCHEMA", "TABLE"]]}])
    got = session(client, database="DB", schema="S").catalog()
    assert got == [{"catalog": "OTHER_DB", "schema": "OTHER_SCHEMA", "name": "T", "type": "table"}]


def test_the_adapter_describes_the_snowflake_engine():
    assert sw.SnowflakeSqlAdapter(client_for=lambda conn: None).describe() is sw.SNOWFLAKE_SQL

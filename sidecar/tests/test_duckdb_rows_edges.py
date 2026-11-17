"""Reading rows on DuckDB 2.0: its branches, run here with a stand-in connection on whatever DuckDB is
installed."""

import pytest

from fabric_desktop import duckdb_rows


class Con:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append(("execute", query, params))
        return self

    def sql(self, query, params=None):
        self.calls.append(("sql", query, params))
        return "relation"

    def complete(self):
        self.calls.append(("complete",))


@pytest.fixture
def two(monkeypatch):
    monkeypatch.setattr(duckdb_rows, "duckdb_major", lambda: 2)


def test_on_2_0_try_cast_keeps_its_null_by_turning_type_pushdown_off(two):
    con = Con()
    assert duckdb_rows.keep_try_cast(con) is con
    assert con.calls == [("execute", "SET disabled_optimizers = 'type_pushdown'", None)]


def test_on_2_0_arrow_comes_from_a_relation(two):
    con = Con()
    assert duckdb_rows.for_arrow(con, "select ?", [1]) == "relation"
    assert con.calls == [("sql", "select ?", [1])]


def test_on_2_0_a_statement_run_through_is_completed(two):
    con = Con()
    duckdb_rows.run_through(con, "create table t (a int)")
    assert con.calls == [("execute", "create table t (a int)", None), ("complete",)]


def test_on_1_x_nothing_is_switched_off_and_no_complete_is_called(monkeypatch):
    monkeypatch.setattr(duckdb_rows, "duckdb_major", lambda: 1)
    con = Con()
    duckdb_rows.keep_try_cast(con)
    duckdb_rows.run_through(con, "select 1", [])
    assert con.calls == [("execute", "select 1", [])]

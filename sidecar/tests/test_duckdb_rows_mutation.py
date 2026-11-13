"""Mutation-testing pins for duckdb_rows (docs/quality/mutation-testing.md, batch 5).

The pinned DuckDB is 1.x, so the 2.0 branches are pinned with a stand-in version and connection."""

from __future__ import annotations

from fabric_desktop import duckdb_rows as R


class _Con:
    def __init__(self):
        self.calls = []

    def sql(self, query, params=None):
        self.calls.append(("sql", query, params))
        return "relation"

    def execute(self, query, params=None):
        self.calls.append(("execute", query, params))
        return "executed"


def test_for_arrow_makes_a_relation_on_duckdb_2(monkeypatch):
    monkeypatch.setattr(R, "duckdb_major", lambda: 2)
    con = _Con()
    assert R.for_arrow(con, "SELECT 1", [1]) == "relation"
    assert con.calls == [("sql", "SELECT 1", [1])]


def test_for_arrow_executes_on_duckdb_1(monkeypatch):
    monkeypatch.setattr(R, "duckdb_major", lambda: 1)
    con = _Con()
    assert R.for_arrow(con, "SELECT 1") == "executed"
    assert R.for_arrow(con, "SELECT ?", [2]) == "executed"
    assert con.calls == [("execute", "SELECT 1", None), ("execute", "SELECT ?", [2])]


def test_arrow_reader_prefers_a_relations_own_reader():
    class Relation:
        def to_arrow_reader(self, rows):
            return ("reader", rows)
    assert R.arrow_reader(Relation(), 5) == ("reader", 5)


def test_arrow_reader_falls_back_to_an_executed_results_batches():
    class Executed:
        def fetch_record_batch(self, rows):
            return ("batches", rows)
    assert R.arrow_reader(Executed(), 7) == ("batches", 7)

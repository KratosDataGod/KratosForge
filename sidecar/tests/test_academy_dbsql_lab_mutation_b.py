"""More dbsql_lab tests, from a mutation run: the answer-length limit at its edge, the words for an
answer too deep to read or that fails to run (once or on the rerun), and how a written table is
compared. Answers run only in the lab's own locked in-memory DuckDB."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop.academy import dbsql_lab
from fabric_desktop.learn import checks

SETUP = "CREATE TABLE t (a INT PRIMARY KEY)"


def test_an_answer_of_exactly_100000_characters_is_read():
    text = "SELECT 1" + " " * (100_000 - len("SELECT 1"))
    assert dbsql_lab.translate(text).strip().upper().startswith("SELECT 1")
    with pytest.raises(ValueError, match="longer than 100,000 characters"):
        dbsql_lab.translate(text + " ")


def test_an_answer_the_reader_recurses_on_is_refused_in_words(monkeypatch):
    def deep(_text):
        raise RecursionError
    monkeypatch.setattr(dbsql_lab, "_databricks", deep)
    with pytest.raises(ValueError, match="^This SQL can't run on this PC: it is nested too deeply to read.$"):
        dbsql_lab.translate("SELECT 1")


def test_sql_that_cant_be_parsed_calls_no_functions():
    assert dbsql_lab.functions("SELECT (((") == set()


def test_table_rows_reads_one_row_past_the_limit(monkeypatch):
    monkeypatch.setattr(checks, "MAX_ROWS", 5)
    con = dbsql_lab.connect(SETUP)
    try:
        cols, rows = dbsql_lab._table_rows(con, ["INSERT INTO t SELECT range FROM range(10)"], "t")
    finally:
        con.close()
    assert cols == ["a"] and len(rows) == 6


def test_compare_refuses_only_a_table_over_the_limit(monkeypatch):
    monkeypatch.setattr(checks, "MAX_ROWS", 3)
    want = (["a"], [(1,), (2,), (3,)])
    assert dbsql_lab._compare("result_matches", "t", (["a"], [(1,), (2,), (3,)]), want, False)["passed"]
    got = dbsql_lab._compare("result_matches", "t", (["a"], [(i,) for i in range(4)]), want, True)
    assert got["detail"] == "After running your SQL twice, t has more than 3 rows; the answer has 3."


def test_compare_names_the_columns():
    want = (["a", "b"], [])
    got = dbsql_lab._compare("result_matches", "t", (["x"], []), want, False)
    assert got["detail"] == "t has columns x; it should have a, b."
    got = dbsql_lab._compare("result_matches", "t", ([], []), want, False)
    assert got["detail"] == "t has columns none; it should have a, b."


def test_compare_counts_the_rows_that_differ():
    got = dbsql_lab._compare("result_matches", "t", (["a"], [(1,), (5,)]), (["a"], [(1,), (2,)]), False)
    assert got["detail"] == "1 of the 2 rows in t differ from the answer's."


def test_compare_says_whether_it_ran_twice():
    same = (["a"], [(1,)])
    assert dbsql_lab._compare("result_matches", "t", same, same, False)["detail"] == "t has the same 1 rows as the answer's."
    assert dbsql_lab._compare("idempotent", "t", same, same, True)["detail"] == "Run twice, t still has the same 1 rows."


def _error(statements):
    con = dbsql_lab.connect(SETUP)
    try:
        con.begin()   # as the check runs it: in one transaction
        with pytest.raises(duckdb.Error) as err:
            for s in statements:
                con.execute(s)
    finally:
        con.close()
    return str(err.value).splitlines()[0]


@pytest.mark.parametrize(("kind", "runs", "said"), [
    ("result_matches", 1, "Your SQL failed to run: "),
    ("idempotent", 2, "Your SQL failed the second time it ran: "),
])
def test_an_answer_that_fails_says_when_and_gives_the_first_line(kind, runs, said):
    answer = "INSERT INTO t VALUES (1)" if kind == "idempotent" else "INSERT INTO t VALUES (1), (1)"
    first = _error([answer] * runs)
    con = dbsql_lab.connect(SETUP)
    try:
        got = dbsql_lab.run_check(con, {"kind": kind, "table": "t"}, answer, "INSERT INTO t VALUES (1)")
    finally:
        con.close()
    assert got == {"kind": kind, "passed": False, "detail": said + first}

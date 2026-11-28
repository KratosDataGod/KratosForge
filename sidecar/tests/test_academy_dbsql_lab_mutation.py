"""academy/dbsql_lab.py's table checks held to their exact words and limits: an answer of 100,000 characters
is read and one more isn't, SQL nested too deeply is refused in words, a table compared after one run or two,
the row limit (made small here) read one row past and compared at exactly the limit, the columns, how many
rows differ, and the first line of an error. Everything runs on an in-memory DuckDB in this process."""

from __future__ import annotations

import pytest

from fabric_desktop.academy import dbsql_lab as d
from fabric_desktop.learn import checks

SETUP = "CREATE TABLE src AS SELECT * FROM range(3) r(x)"
SOLUTION = "CREATE OR REPLACE TABLE t AS SELECT x FROM src"


@pytest.fixture
def con():
    c = d.connect(SETUP)
    yield c
    c.close()


def check(con, answer, kind="result_matches"):
    return d.run_check(con, {"kind": kind, "table": "t"}, answer, SOLUTION)


def test_an_answer_of_100000_characters_is_read_and_one_more_isnt():
    text = "SELECT 1" + " " * (100_000 - 8)
    assert len(text) == d.MAX_ANSWER == 100_000
    assert d.translate(text).strip().upper().startswith("SELECT 1")
    with pytest.raises(ValueError) as caught:
        d.translate(text + " ")
    assert str(caught.value) == "This SQL can't run on this PC: it is longer than 100,000 characters."


def test_sql_nested_too_deeply_to_read_is_refused_in_words(monkeypatch):
    def deep(text):
        raise RecursionError

    monkeypatch.setattr(d, "_databricks", deep)
    with pytest.raises(ValueError) as caught:
        d.translate("SELECT 1")
    assert str(caught.value) == "This SQL can't run on this PC: it is nested too deeply to read."


def test_sql_it_cant_parse_calls_no_functions():
    assert d.functions("SELECT (") == set()


def test_a_table_is_compared_after_one_run_or_two(con):
    assert check(con, SOLUTION)["detail"] == "t has the same 3 rows as the answer's."
    assert check(con, SOLUTION, "idempotent")["detail"] == "Run twice, t still has the same 3 rows."


def test_an_answer_that_fails_says_when_with_the_first_line_of_the_error(con):
    got = check(con, "CREATE TABLE t AS SELECT x FROM src", "idempotent")
    assert got == {"kind": "idempotent", "passed": False,
                   "detail": 'Your SQL failed the second time it ran: Catalog Error: Table with name "t" already exists!'}
    got = check(con, "CREATE OR REPLACE TABLE t AS SELECT * FROM nope")
    assert got["detail"].startswith("Your SQL failed to run: Catalog Error: Table with name nope does not exist!")
    assert "\n" not in got["detail"]


def test_wrong_columns_and_rows_that_differ_are_counted(con):
    assert check(con, "CREATE OR REPLACE TABLE t AS SELECT x AS y FROM src")["detail"] == "t has columns y; it should have x."
    got = check(con, "CREATE OR REPLACE TABLE t AS SELECT x FROM src WHERE x < 2 UNION ALL SELECT 9")
    assert got["detail"] == "1 of the 3 rows in t differ from the answer's."


def test_a_table_with_no_columns_is_named_as_none():
    got = d._compare("result_matches", "t", ([], []), (["x"], [(1,)]), again=False)
    assert got["detail"] == "t has columns none; it should have x."


@pytest.mark.parametrize("rows, said", [
    (3, "t has the same 3 rows as the answer's."),
    (4, "t has more than 3 rows; the answer has 3."),
])
def test_a_table_is_read_one_row_past_the_limit_and_compared_at_it(con, monkeypatch, rows, said):
    monkeypatch.setattr(checks, "MAX_ROWS", 3)
    answer = f"CREATE OR REPLACE TABLE t AS SELECT x FROM range({rows}) r(x)"
    solution = "CREATE OR REPLACE TABLE t AS SELECT x FROM range(3) r(x)"
    got = d.run_check(con, {"kind": "result_matches", "table": "t"}, answer, solution)
    assert got["detail"] == said
    cols, fetched = d._table_rows(con, [answer], "t")
    assert cols == ["x"] and len(fetched) == min(rows, 4)

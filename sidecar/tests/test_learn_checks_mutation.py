"""Learn checks (learn/checks.py): the edges mutation testing found unpinned. Each test uses its own
in-memory DuckDB; nothing is written to disk."""

import duckdb
import pytest

from fabric_desktop.learn import checks


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, name)")
    yield c
    c.close()


def test_an_answer_is_compared_row_by_row_up_to_a_million_rows():
    assert checks.MAX_ROWS == 1_000_000


def test_an_answer_with_more_rows_than_the_limit_is_refused(con, monkeypatch):
    monkeypatch.setattr(checks, "MAX_ROWS", 2)
    out = checks.result_matches(con, "SELECT id FROM t", expected={"columns": ["id"], "rows": [[1], [2], [3]]})
    assert out == {"kind": "result_matches", "passed": False,
                   "detail": "Your query didn't run: the query returns more than 2 rows; a lab answer is smaller"}


def test_an_answer_with_exactly_the_limit_of_rows_is_compared(con, monkeypatch):
    monkeypatch.setattr(checks, "MAX_ROWS", 3)
    out = checks.result_matches(con, "SELECT id FROM t", expected={"columns": ["id"], "rows": [[1], [2], [3]]})
    assert out["passed"] is True and out["detail"] == "3 rows, the same as the answer."


def test_wrong_columns_are_named(con):
    out = checks.result_matches(con, "SELECT name FROM t", reference="SELECT id FROM t")
    assert out["detail"] == "The columns are name; the question asks for id."


def test_the_count_of_differing_rows_is_how_many_answer_rows_are_missing(con):
    out = checks.result_matches(con, "SELECT id FROM t WHERE id < 3 UNION ALL SELECT 9",
                                expected={"columns": ["id"], "rows": [[1], [2], [3]]})
    assert out["detail"] == "1 of the 3 rows differ from the answer."


def test_the_detail_says_same_order_only_when_order_was_checked(con):
    exp = {"columns": ["id"], "rows": [[1], [2], [3]]}
    assert checks.result_matches(con, "SELECT id FROM t ORDER BY id", expected=exp, ordered=True)["detail"] == \
        "3 rows, the same as the answer and in the same order."
    assert checks.result_matches(con, "SELECT id FROM t ORDER BY id", expected=exp)["detail"] == \
        "3 rows, the same as the answer."


def test_a_defects_query_that_doesnt_run_fails_with_why(con):
    out = checks.defects_found(con, "SELECT nope FROM t", [{"table": "t", "key": 1, "kind": "dup"}])
    assert out["passed"] is False and out["detail"].startswith("Your query didn't run: ")


def clock(*durations):
    """A clock whose readings make each timed run take the next of ``durations`` seconds."""
    readings, now = [], 0.0
    for d in durations:
        readings += [now, now + d]
        now += d + 1.0
    it = iter(readings)
    return lambda: next(it)


def test_speed_is_measured_over_three_runs_each_by_default(con):
    out = checks.faster_than(con, "SELECT id FROM t", "SELECT id FROM t", factor=1.0,
                             clock=clock(*[2.0, 1.0] * 3))
    assert "fastest of 3 runs each" in out["detail"]


def test_no_runs_asked_for_still_times_one_each(con):
    out = checks.faster_than(con, "SELECT id FROM t", "SELECT id FROM t", factor=1.0, runs=0, clock=clock(2.0, 1.0))
    assert out["measured"]["speedup"] == 2.0


def test_exactly_the_goal_passes_and_times_are_rounded_to_four_places(con):
    out = checks.faster_than(con, "SELECT id FROM t", "SELECT id FROM t", factor=2.0, runs=1,
                             clock=clock(2.46912, 1.23456))
    assert out["passed"] is True
    assert out["measured"] == {"seconds": 1.2346, "baseline_seconds": 2.4691, "speedup": 2.0}


def test_a_speedup_is_rounded_to_two_places(con):
    out = checks.faster_than(con, "SELECT id FROM t", "SELECT id FROM t", factor=1.0, runs=1, clock=clock(1.0, 0.75))
    assert out["measured"]["speedup"] == 1.33


def test_a_run_too_quick_to_time_counts_as_infinitely_faster(con):
    out = checks.faster_than(con, "SELECT id FROM t", "SELECT id FROM t", factor=2.0, runs=1, clock=clock(1.0, 0.0))
    assert out["passed"] is True
    assert out["measured"] == {"seconds": 0.0, "baseline_seconds": 1.0, "speedup": None}

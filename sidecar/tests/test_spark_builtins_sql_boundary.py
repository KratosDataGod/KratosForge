"""Spark SQL built-ins on this PC at their exact edges (boundary re-score of batch 8, mutation batch 158,
docs/quality/mutation-testing.md). Expected values are what Spark gives for the same query."""

from __future__ import annotations

from fabric_desktop import sql


def one(query: str):
    return next(iter(sql.run(query).to_pylist()[0].values()))


def test_elt_with_one_choice():
    assert one("SELECT elt(1, 'a')") == "a"


def test_shiftleft_of_a_literal_just_past_int_is_a_long():
    assert one("SELECT shiftleft(2147483648, 1)") == 4294967296
    assert one("SELECT shiftleft(2147483647, 1)") == -2  # an int, wrapping as Java's does


def test_window_drops_a_row_with_no_time():
    assert one("SELECT count(*) FROM (SELECT window(t, '1 hour') AS w "
               "FROM VALUES (TIMESTAMP '2026-01-01 00:30:00'), (NULL) AS v(t))") == 1


def test_shiftleft_of_the_smallest_int_literal_stays_an_int():
    assert one("SELECT shiftleft(-2147483648, 1)") == 0  # Spark reads -2147483648 as an int

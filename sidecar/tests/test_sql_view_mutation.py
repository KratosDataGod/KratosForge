"""Edges of sql_view that the main tests leave open (mutation batch 75)."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest

from fabric_desktop import sql_view
from fabric_desktop.frames import Frames

T = pa.table({"n": pa.array([3, 1, 2], pa.int64()), "s": ["c", "a", "b"]})


def test_at_most_three_sort_keys_are_read_and_the_rest_ignored():
    assert sql_view.MAX_SORT_KEYS == 3
    keys = [{"column": 0}, {"column": 1}, {"column": 0, "desc": True}, {"column": 99}]
    rows, total = sql_view.view_page(T, 0, 10, keys, None)
    assert ([r[0] for r in rows], total) == ([1, 2, 3], 3)
    with pytest.raises(ValueError, match="sort names a column"):
        sql_view.view_page(T, 0, 10, keys[2:], None)


def test_a_duration_column_is_filtered_as_its_text():
    t = pa.table({"d": pa.array([dt.timedelta(seconds=5), dt.timedelta(minutes=2)], pa.duration("us"))})
    rows, total = sql_view.view_page(t, 0, 10, None, [{"column": 0, "text": "> 1"}])
    assert total == 0 and rows == []  # compared as text, not refused as a date
    sql, params = sql_view.filter_sql("c0", t.schema.field(0), 0, "> 1")
    assert (sql, params) == ("CAST(c0 AS VARCHAR) > ?", ["1"])


def test_a_filter_of_exactly_200_characters_is_read_and_201_is_refused():
    field = pa.field("s", pa.string())
    sql, params = sql_view.filter_sql("c1", field, 1, "x" * 200)
    assert params == ["%" + "x" * 200 + "%"]
    assert sql_view.filter_sql("c1", field, 1, "  " + "y" * 200 + "  ")[1] == ["%" + "y" * 200 + "%"]
    with pytest.raises(sql_view.FilterError, match="at most 200"):
        sql_view.filter_sql("c1", field, 1, "x" * 201)


def test_a_filter_or_sort_on_the_position_just_past_the_last_column_is_refused():
    with pytest.raises(ValueError, match="filter names a column"):
        sql_view.where_parts(T, ["c0", "c1"], [{"column": 2, "text": "x"}])
    with pytest.raises(ValueError, match="filter names a column"):
        sql_view.where_parts(T, ["c0", "c1"], [{"column": 2, "text": ""}])
    with pytest.raises(ValueError, match="sort names a column"):
        sql_view.view_page(T, 0, 10, [{"column": 2}], None)
    parts, params = sql_view.where_parts(T, ["c0", "c1"], [{"column": 1, "text": "a"}])
    assert len(parts) == 1 and params == ["%a%"]


@pytest.mark.parametrize("profile, expected", [
    ({"distinct": 10, "missing": 3}, 2),     # never more than the values there are
    ({"distinct": 1, "missing": 3}, 1),
    ({"distinct": None, "missing": 0}, 0),   # none counted reads as 0
    ({"distinct": 10, "missing": None}, 5),  # none missing
    ({"distinct": 10}, 5),
])
def test_distinct_is_capped_at_the_rows_that_have_a_value(monkeypatch, profile, expected):
    monkeypatch.setattr(Frames, "_profile_column", staticmethod(lambda con, c, field, rows: dict(profile)))
    t = pa.table({"x": [1, 2, 3, 4, 5]})
    assert sql_view.column_stats(t, 0)["distinct"] == expected

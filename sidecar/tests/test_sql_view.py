"""The result grid's sort, filter and column statistics over a run's snapshot (sql_view.py, S19 S20 S26)."""

from __future__ import annotations

import datetime as dt
import decimal

import pyarrow as pa
import pytest

from fabric_desktop import sql_view

T = pa.table([
    pa.array(["West", "North", "South", "East", "Central", None]),
    pa.array([4812, 3977, 3105, 2640, 1288, None], pa.int64()),
    pa.array([decimal.Decimal("1204330.50"), decimal.Decimal("998120.00"), decimal.Decimal("741880.25"),
              decimal.Decimal("612004.75"), decimal.Decimal("301550.10"), None], pa.decimal128(18, 2)),
    pa.array([dt.date(2026, 10, 1), dt.date(2026, 10, 5), dt.date(2026, 9, 30), None, dt.date(2026, 11, 1),
              dt.date(2026, 10, 1)]),
    pa.array([True, False, True, None, False, True]),
    pa.array([4812, 1, 2, 3, 4, 5], pa.int64()),  # the same name as column 1: names can repeat
], names=["region", "orders", "revenue", "since", "active", "orders"])


def page(sort=None, filters=None, offset=0, limit=100):
    rows, total = sql_view.view_page(T, offset, limit, sort, filters)
    return rows, total


def regions(sort=None, filters=None):
    rows, total = page(sort, filters)
    return [r[0] for r in rows], total


def test_sort_by_a_column_either_way_with_empty_values_last():
    assert regions([{"column": 1, "desc": True}])[0] == ["West", "North", "South", "East", "Central", None]
    assert regions([{"column": 1}])[0] == ["Central", "East", "South", "North", "West", None]
    assert regions([{"column": 0}])[0] == ["Central", "East", "North", "South", "West", None]


def test_ties_keep_the_snapshot_order_and_a_second_key_breaks_them():
    assert regions([{"column": 3}])[0] == ["South", "West", None, "North", "Central", "East"]
    assert regions([{"column": 3}, {"column": 1, "desc": True}])[0][:3] == ["South", "West", None]


def test_a_repeated_column_name_sorts_by_its_own_position():
    assert regions([{"column": 5}])[0] == ["North", "South", "East", "Central", None, "West"]


@pytest.mark.parametrize("column,text,expected", [
    (0, "!= Central", ["West", "North", "South", "East"]),
    (0, "<> central", ["West", "North", "South", "East"]),
    (0, "= west", ["West"]),
    (0, "st", ["West", "East"]),  # contains, ignoring case
    (0, "null", [None]),
    (0, "not null", ["West", "North", "South", "East", "Central"]),
    (1, "> 3000", ["West", "North", "South"]),
    (1, ">= 3,105", ["West", "North", "South"]),
    (1, "3977", ["North"]),  # a number column: equals a bare number
    (1, "4,812", ["West"]),  # thousands separators are fine
    (2, "< 700000", ["East", "Central"]),
    (3, ">= 2026-10-01", ["West", "North", "Central", None]),
    (3, "= 2026-10-01", ["West", None]),
    (4, "= true", ["West", "South", None]),
    (4, "!= TRUE", ["North", "Central"]),
    (0, "50%", []),  # % and _ are literal, not wildcards
])
def test_a_filter_box_reads_operators_for_the_column_s_type(column, text, expected):
    assert regions(None, [{"column": column, "text": text}])[0] == expected


def test_filters_on_several_columns_all_apply_and_count_what_matches():
    rows, total = page([{"column": 1, "desc": True}], [{"column": 0, "text": "!= Central"}, {"column": 1, "text": "> 3000"}])
    assert [r[0] for r in rows] == ["West", "North", "South"] and total == 3
    rows, total = page([{"column": 1, "desc": True}], [{"column": 1, "text": "> 1000"}], offset=1, limit=2)
    assert [r[0] for r in rows] == ["North", "South"] and total == 5


@pytest.mark.parametrize("column,text,message", [
    (1, "> lots", "Write a number after > in the filter on orders"),
    (3, "< 1 Oct", "Write a date after < in the filter on since, as 2026-10-01"),
    (4, "> true", "takes = true"),
    (0, "=", "Write a value after ="),
    (0, "x" * 201, "too long"),
])
def test_a_filter_that_can_t_be_read_names_its_column(column, text, message):
    with pytest.raises(sql_view.FilterError, match=message) as err:
        page(None, [{"column": column, "text": text}])
    assert err.value.column == column


def test_a_filter_s_text_is_a_parameter_never_sql():
    assert regions(None, [{"column": 0, "text": "'; DROP TABLE t; --"}])[0] == []
    assert regions(None, [{"column": 0, "text": "= ' OR 1=1 --"}])[0] == []
    # Compared as text, so letters sort after "1": every region, and the operator text stayed a value.
    assert regions(None, [{"column": 0, "text": "> 1) OR (1=1"}])[0] == ["West", "North", "South", "East", "Central"]
    assert page()[1] == 6


def test_a_sort_or_filter_on_a_column_the_result_lacks_is_refused():
    with pytest.raises(ValueError, match="doesn't have"):
        page([{"column": 9}])
    with pytest.raises(ValueError, match="doesn't have"):
        page(None, [{"column": -1, "text": "x"}])


def test_times_compare_as_times_and_a_bad_column_index_is_refused():
    t = pa.table({"at": pa.array([dt.time(1, 30), dt.time(2, 15), dt.time(23, 0), None])})
    rows, total = sql_view.view_page(t, 0, 10, None, [{"column": 0, "text": "> 02:00"}])
    assert total == 2 and [r[0] for r in rows] == [dt.time(2, 15), dt.time(23, 0)]
    with pytest.raises(sql_view.FilterError, match="Write a time after < in the filter on at, as 14:30"):
        sql_view.view_page(t, 0, 10, None, [{"column": 0, "text": "< 2pm"}])
    for bad in (None, "x", [1], True):
        with pytest.raises(ValueError, match="doesn't have"):
            sql_view.view_page(t, 0, 10, [{"column": bad}], None)


def test_lists_and_structs_can_t_be_sorted_but_can_be_searched():
    t = pa.table({"tags": pa.array([["a", "b"], ["c"]])})
    with pytest.raises(ValueError, match="lists or structs"):
        sql_view.view_page(t, 0, 10, [{"column": 0}], None)
    rows, total = sql_view.view_page(t, 0, 10, None, [{"column": 0, "text": "c"}])
    assert total == 1 and rows == [[["c"]]]


def test_column_statistics_cover_every_row():
    s = sql_view.column_stats(T, 1)
    assert s["rows"] == 6 and s["missing"] == 1 and s["distinct"] == 5 and s["column"] == 1
    assert s["min"] == 1288 and s["max"] == 4812 and s["mean"] == pytest.approx(3164.4)
    assert sum(s["histogram"]) == 5
    text = sql_view.column_stats(T, 0)
    assert text["kind"] == "text" and len(text["top"]) == 5 and "histogram" not in text
    when = sql_view.column_stats(T, 3)
    assert when["min"] == "2026-09-30" and when["max"] == "2026-11-01"
    with pytest.raises(ValueError, match="isn't in this result"):
        sql_view.column_stats(T, 6)


def test_distinct_never_exceeds_the_values_there_are():
    t = pa.table({"n": pa.array(range(1200), pa.int64())})
    s = sql_view.column_stats(t, 0)
    assert s["distinct"] <= 1200 and s["rows"] == 1200


def test_results_over_the_limit_say_how_to_go_on():
    sql_view.check_size(sql_view.VIEW_MAX_ROWS, "Sorting")
    with pytest.raises(ValueError, match="up to 5,000,000 rows, and this one has 5,000,001"):
        sql_view.check_size(sql_view.VIEW_MAX_ROWS + 1, "Sorting")


@pytest.mark.parametrize("sort, filters, match", [
    (None, ["oops"], "The filter isn't in the form the grid sends"),
    (None, [None], "The filter isn't in the form the grid sends"),
    (None, {"column": 0, "text": "1"}, "The filter isn't in the form the grid sends"),
    (["n"], None, "The sort isn't in the form the grid sends"),
    ({"column": 0}, None, "The sort isn't in the form the grid sends"),
    (None, [{"column": 0.5, "text": "1"}], "A filter names a column this result doesn't have"),
    ([{"column": 1.5}], None, "The sort names a column this result doesn't have"),
])
def test_a_sort_or_filter_the_grid_never_sends_is_refused_by_name(sort, filters, match):
    with pytest.raises(ValueError, match=match):
        sql_view.view_page(pa.table({"n": [1, 2]}), 0, 10, sort, filters)


def test_a_whole_float_still_names_its_column():
    assert sql_view.view_page(pa.table({"n": [1, 2]}), 0, 10, [{"column": 0.0, "desc": True}], None)[0] == [[2], [1]]

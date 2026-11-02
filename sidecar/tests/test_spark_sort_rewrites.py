"""sort_array and array_sort answer on DuckDB with Spark's order and NULL placement, on every
sqlglot the engine allows: 30.24 folded SortArray into ArraySort, which moved how each is read."""

from __future__ import annotations

import duckdb
import pytest
import sqlglot

from fabric_desktop import sql

ARRAY = "ARRAY(3, NULL, 1, 2)"


def _duckdb(spark: str) -> str:
    return sql.spark_fixes(sqlglot.parse_one(spark, read="spark")).sql("duckdb")


@pytest.mark.parametrize("spark, answer", [
    (f"SELECT sort_array({ARRAY})", [None, 1, 2, 3]),             # ascending: NULL first
    (f"SELECT sort_array({ARRAY}, true)", [None, 1, 2, 3]),
    (f"SELECT sort_array({ARRAY}, false)", [3, 2, 1, None]),      # descending: NULL last
    (f"SELECT array_sort({ARRAY})", [1, 2, 3, None]),             # array_sort: NULL last
])
def test_sorts_put_null_where_spark_does(spark, answer):
    assert duckdb.sql(_duckdb(spark)).fetchall()[0][0] == answer


def test_array_sort_with_a_comparator_is_sorted_by_the_comparator_not_a_nulls_rule():
    # #1413: the comparator decides where NULL goes, so no NULLS FIRST/LAST rule is added.
    out = _duckdb("SELECT array_sort(a, (l, r) -> r - l) FROM t").upper()
    assert "NULLS FIRST" not in out and "NULLS LAST" not in out and "KF_C < 0" in out


def test_sort_array_with_a_column_for_the_order_is_left_alone():
    out = _duckdb("SELECT sort_array(a, flag) FROM t")
    assert "NULLS" not in out.upper()

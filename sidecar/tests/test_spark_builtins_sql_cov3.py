"""Spark SQL built-ins (spark_builtins_sql) at the edges the rest of the suite leaves untaken: a window()
in the select list, hash of the smallest int, to_timestamp with a computed pattern, extract from a zoned
timestamp, ANY over a subquery, and the helpers' guards called directly. Nothing here asserts on a call
missing an argument (open PRs change that)."""

from __future__ import annotations

import datetime as dt

import pytest
import sqlglot
from sqlglot import exp

from fabric_desktop import spark_builtins_sql as sb
from fabric_desktop import spark_sql_names, sql


def written(query: str) -> str:
    return sql.duckdb_sql(sql.parse(query)[0])


def rows(query: str) -> list[tuple]:
    return [tuple(r.values()) for r in sql.run(query).to_pylist()]


def test_a_window_in_the_select_list_is_named_window():
    query = ("SELECT window(t, '1 hour'), count(*) FROM (SELECT TIMESTAMP '2024-01-01 00:10:00' AS t) "
             "GROUP BY window(t, '1 hour')")
    out = sql.run(query).to_pylist()
    assert out == [{"window": {"start": dt.datetime(2024, 1, 1, 0, 0), "end": dt.datetime(2024, 1, 1, 1, 0)},
                    "count(1)": 1}]


def test_hash_of_the_smallest_int_hashes_it_as_an_int():
    smallest = sb._spark_literal(exp.Neg(this=exp.Literal.number("2147483648")))
    assert isinstance(smallest, exp.Cast) and smallest.to.is_type(exp.DataType.Type.INT)
    other = exp.Neg(this=exp.Literal.number("5"))
    assert sb._spark_literal(other) is other
    assert rows("SELECT hash(-2147483648) = hash(CAST(-2147483648 AS INT))") == [(True,)]


def test_to_timestamp_with_a_computed_pattern_is_left_to_sqlglot():
    text = written("SELECT to_timestamp(s, f) FROM (SELECT '2024' AS s, 'yyyy' AS f)")
    assert "TRY_STRPTIME(s, f)" in text


def test_to_timestamp_ntz_with_a_pattern():
    assert rows("SELECT to_timestamp_ntz('2024', 'yyyy')") == [(dt.datetime(2024, 1, 1),)]


def test_extract_from_a_zoned_timestamp_reads_its_utc_time():
    assert rows("SELECT extract(HOUR FROM to_utc_timestamp('2024-01-01 10:00:00', 'UTC'))") == [(10,)]


def test_any_over_a_subquery_is_a_comparison_not_an_aggregate():
    assert rows("SELECT 1 = ANY(SELECT 1)") == [(True,)]
    assert "BOOL_OR" not in written("SELECT any((SELECT true))")


def test_to_date_with_a_pattern():
    assert rows("SELECT to_date('2024', 'yyyy')") == [(dt.date(2024, 1, 1),)]


# ---------------------------------------------------------------- helpers


def test_initcap_with_a_delimiter_other_than_a_space_is_left_as_it_is():
    node = exp.Initcap(this=exp.column("s"), expression=exp.Literal.string("-"))
    assert sb._initcap(node, []) is None


def test_levenshtein_with_costs_or_a_cap_is_left_as_it_is():
    node = exp.Levenshtein(this=exp.column("a"), expression=exp.column("b"), max_dist=exp.Literal.number(2))
    assert sb._levenshtein(node, []) is None


def test_a_pattern_python_cant_read_isnt_taken_for_an_empty_match():
    assert sb._empty_match("(") is False
    assert sb._empty_match("a*") is True


def test_a_written_pattern_that_cant_be_read_alone_is_none():
    node = exp.Anonymous(this="date_format", expressions=[])
    node.meta[spark_sql_names._ARGS] = ["t", "("]
    assert sb.written_pattern(node) is None
    node.meta[spark_sql_names._ARGS] = ["t", "'yyyy'"]
    assert sb.written_pattern(node) == "yyyy"


def test_to_date_with_a_format_not_written_out_reads_it_as_strftime():
    node = sqlglot.parse_one("SELECT TO_DATE(s, 'yyyy')", read="spark").expressions[0]
    node.meta.pop(spark_sql_names._ARGS, None)
    out = sb._to_date_with_format(node, [])
    assert isinstance(out, exp.Cast) and out.to.is_type(exp.DataType.Type.DATE)
    assert isinstance(out.this.this, exp.StrToTime) and out.this.this.args.get("safe") is True


def test_a_hash_that_lost_its_mark_stops_the_cell():
    with pytest.raises(ValueError, match=r"xxhash64\(\) can't run on this PC"):
        sb._no_duckdb_hash(sqlglot.parse_one("SELECT XXHASH64(1)", read="duckdb"))
    sb._no_duckdb_hash(sqlglot.parse_one("SELECT OTHER(1)", read="duckdb"))

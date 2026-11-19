"""Spark SQL built-ins (spark_builtins_sql) at the edges the rest of the suite leaves untaken: a call with
an argument count Spark doesn't take is left as written, for DuckDB to refuse (Spark refuses it too), and
calls written in the less usual ways give Spark's answer."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import sql


def written(query: str) -> str:
    return sql.duckdb_sql(sql.parse(query)[0])


def rows(query: str) -> list[tuple]:
    return [tuple(r.values()) for r in sql.run(query).to_pylist()]


@pytest.mark.parametrize(
    ("query", "kept"),
    [
        ("SELECT monotonically_increasing_id(1)", "MONOTONICALLY_INCREASING_ID(1)"),
        ("SELECT json_tuple('{}')", "JSON_TUPLE('{}')"),
        ("SELECT mask('Ab1', 1)", "MASK('Ab1', 1)"),
        ("SELECT from_csv('1', 'a INT', map())", "FROM_CSV('1', 'a INT', MAP([], []))"),
        ("SELECT from_csv('1', 'a INT, <')", "FROM_CSV('1', 'a INT, <')"),
        ("SELECT from_csv('1', 'a')", "FROM_CSV('1', 'a')"),
        ("SELECT make_dt_interval(1, 2, 3, 4, 5)", "MAKE_DT_INTERVAL(1, 2, 3, 4, 5)"),
        (
            "SELECT to_timestamp_ntz('2020', 'yyyy', 1)",
            "TO_TIMESTAMP_NTZ('2020', 'yyyy', 1)",
        ),
        ("SELECT from_csv('1', 'a INT, b')", "FROM_CSV('1', 'a INT, b')"),
        ("SELECT make_timestamp(2020, 1, 1, 0, 0)", "MAKE_TIMESTAMP(2020, 1, 1, 0, 0)"),
        ("SELECT to_varchar(1)", "TO_VARCHAR(1)"),
        ("SELECT to_varchar(1, '9', 3)", "TO_VARCHAR(1, '9', 3)"),
        ("SELECT try_to_number('1')", "TRY_TO_NUMBER('1')"),
        ("SELECT try_to_number('1', '9', 1)", "TRY_TO_NUMBER('1', '9', 1)"),
    ],
)
def test_a_call_spark_doesnt_take_is_left_as_written_and_refused(query, kept):
    assert kept in written(query)
    with pytest.raises(duckdb.Error):
        sql.run(query)


def test_octet_length_of_two_arguments_is_left_to_duckdb_to_refuse():
    assert "OCTET_LENGTH('a', 'b')" in written("SELECT octet_length('a', 'b')")
    with pytest.raises(duckdb.Error):
        sql.run("SELECT octet_length('a', 'b')")


def test_to_date_with_a_pattern_from_a_column_is_read_by_strptime():
    assert "TRY_STRPTIME('2020-01-01', fmt)" in written(
        "SELECT to_date('2020-01-01', fmt) FROM (SELECT 'yyyy-MM-dd' fmt)"
    )


def test_grouping_id_counts_each_grouped_column_once():
    assert "GROUPING_ID(id)" in written(
        "SELECT grouping_id() FROM range(2) GROUP BY id, id"
    )
    assert rows("SELECT grouping_id() FROM range(2) GROUP BY id, id") == [(0,), (0,)]


def test_grouping_id_with_no_group_by_is_left_as_written():
    assert "GROUPING_ID()" in written("SELECT grouping_id() FROM range(2)")


def test_any_as_an_aggregate_is_bool_or():
    assert rows("SELECT any(id > 0) FROM range(2)") == [(True,)]
    assert rows("SELECT any(id > 5) FROM range(2)") == [(False,)]


def test_hash_of_the_smallest_int_hashes_it_as_an_int():
    assert rows("SELECT hash(-2147483648)") == rows(
        "SELECT hash(CAST(-2147483648 AS INT))"
    )


def test_extract_from_a_timestamp_column_reads_the_field():
    assert rows(
        "SELECT extract(year FROM ts) FROM (SELECT timestamp'2020-01-01' ts)"
    ) == [(2020,)]


def test_make_timestamp_with_a_zone_from_a_column_reads_it_there():
    out = rows(
        "SELECT CAST(make_timestamp(2020, 1, 1, 0, 0, 0, tz) AS STRING) FROM (SELECT 'UTC' tz)"
    )
    assert out == [("2020-01-01 00:00:00",)]


def test_to_timestamp_spellings_with_a_pattern_read_the_text_with_it():
    assert (
        rows("SELECT to_timestamp_ntz('2020-01-02', 'yyyy-MM-dd')")[0][0].isoformat()
        == "2020-01-02T00:00:00"
    )
    for name in ("try_to_timestamp", "to_timestamp_ltz"):
        out = rows(f"SELECT CAST({name}('2020-01-02', 'yyyy-MM-dd') AS STRING)")
        assert out == [("2020-01-02 00:00:00",)]


def test_window_in_the_select_list_is_named_window():
    out = rows(
        "SELECT window(ts, '1 hour') FROM (SELECT timestamp'2020-01-01 10:30:00' ts)"
    )
    assert [(w["start"].isoformat(), w["end"].isoformat()) for (w,) in out] == [
        ("2020-01-01T10:00:00", "2020-01-01T11:00:00")
    ]


def test_a_subquery_beside_a_grouped_window_keeps_its_own_columns():
    out = rows(
        "SELECT count(*), (SELECT max(x) FROM (SELECT 1 x)) FROM "
        "(SELECT timestamp'2020-01-01 10:30:00' ts) GROUP BY window(ts, '1 hour')"
    )
    assert out == [(1, 1)]


def test_extract_from_an_expression_of_unknown_type_reads_it_as_a_timestamp():
    assert rows(
        "SELECT extract(year FROM ts + INTERVAL 1 DAY) FROM (SELECT timestamp'2020-12-31' ts)"
    ) == [(2021,)]


def test_grouping_id_by_an_empty_grouping_is_left_as_written():
    assert "GROUPING_ID()" in written("SELECT grouping_id() FROM range(2) GROUP BY ()")


def test_builders_hand_back_nothing_for_an_argument_count_they_dont_write():
    # sqlglot refuses these counts before a builder sees them; a builder called anyway leaves the call alone.
    from sqlglot import exp

    from fabric_desktop import spark_builtins_sql as builtins

    one_arg = [exp.Literal.string("a")]
    assert builtins._sha2(exp.Anonymous(this="sha2"), one_arg) is None
    assert builtins._typeof(exp.Anonymous(this="typeof"), one_arg * 2) is None
    assert builtins._elt(exp.Anonymous(this="elt"), one_arg) is None
    unnamed = exp.StrToTime(
        this=exp.Literal.string("2020"), format=exp.Literal.string("%Y")
    )
    assert builtins._to_timestamp_with_format(unnamed, []) is None

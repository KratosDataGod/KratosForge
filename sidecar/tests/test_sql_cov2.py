"""Spark SQL translation in sql.py at the edges the rest of the suite leaves untaken: a function that is
the whole expression handed in, refusals for what can't run here, and options and patterns written
oddly. Each test checks the SQL written, the value DuckDB gives back or the refusal's text."""

from __future__ import annotations

import duckdb
import pytest
import sqlglot

from fabric_desktop import sql


def spark(text: str) -> sqlglot.exp.Expression:
    return sqlglot.parse_one(text, read="spark")


def rows(query: str) -> list[tuple]:
    return [tuple(r.values()) for r in sql.run(query).to_pylist()]


def one(query: str):
    return rows(query)[0][0]


def value(query: str):
    """The first value of ``query``, on a connection of its own: DuckDB's default one is shared with every
    test in this process, and on DuckDB 2 a result left unread there stops the next test that uses it."""
    with duckdb.connect() as con:
        return con.execute(query).fetchone()[0]


def test_next_day_as_the_whole_expression_is_left_for_sqlglot():
    out = sql.duckdb_sql(
        sql._spark_date_results(spark("next_day(DATE '2020-01-01', 'Mon')"), False)
    )
    assert value(f"SELECT {out}").isoformat() == "2020-01-06"


def test_a_cast_function_as_the_whole_expression_becomes_a_try_cast():
    out = sql.spark_legacy_casts(spark("double('1')"))
    assert isinstance(out, sqlglot.exp.TryCast)
    assert value(f"SELECT {sql.duckdb_sql(out)}") == 1.0


def test_timestamp_text_drops_the_offset_and_leaves_other_values_as_they_are():
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    zoned = sql.spark_timestamp_text("TIMESTAMPTZ '2020-01-01 10:00:00+00'")
    assert con.execute(f"SELECT {zoned}").fetchone()[0] == "2020-01-01 10:00:00"
    assert con.execute(f"SELECT {sql.spark_timestamp_text('42')}").fetchone()[0] == "42"


def test_a_cast_of_a_parenthesised_number_past_tinyint_wraps_as_spark_does():
    assert one("SELECT CAST((300) AS TINYINT)") == 44


def test_a_cast_from_a_decimal_past_tinyint_wraps_as_spark_does():
    assert one("SELECT CAST(CAST(300 AS DECIMAL(5,0)) AS TINYINT)") == 44


def test_datediff_with_a_unit_counts_in_that_unit():
    assert one("SELECT datediff(day, '2020-01-01', '2020-02-01')") == 31


def test_array_sort_with_a_comparator_keeps_its_order():
    assert one("SELECT array_sort(array(3, 1, 2), (a, b) -> b - a)") == [3, 2, 1]


def test_schema_of_json_as_the_whole_expression_is_its_text():
    out = sql._spark_json(False, spark("""schema_of_json('{"a":1}')"""))
    assert sql.duckdb_sql(out) == "'STRUCT<a: BIGINT>'"


def test_schema_of_json_options_written_as_numbers_are_refused():
    with pytest.raises(
        ValueError,
        match=r"schema_of_json's options must be written out as map\('name', 'value'",
    ):
        sql.run("""SELECT schema_of_json('{"a":1}', map('prefersDecimal', 1))""")


def test_from_json_refusals_name_what_cant_run():
    with pytest.raises(ValueError, match=r"from_json's options must be written out"):
        sql.run("""SELECT from_json('{"a":1}', 'a INT', map('mode', 1))""")
    with pytest.raises(
        ValueError,
        match=r"from_json reads a MAP with STRING keys, as Spark does, not MAP<INT, INT>",
    ):
        sql.run("""SELECT from_json('{"a":1}', 'MAP<INT, INT>')""")
    with pytest.raises(
        ValueError,
        match=r"from_json reads a STRUCT, ARRAY or MAP schema, as Spark does, not INT",
    ):
        sql.run("SELECT from_json('1', 'INT')")


def test_get_json_object_refuses_a_key_with_a_quote():
    with pytest.raises(
        ValueError, match=r"get_json_object can't read the key 'a\"b' on this PC"
    ):
        sql.run("""SELECT get_json_object('{"a":1}', '$.a"b')""")


def test_a_date_cast_as_the_whole_expression_is_read_by_sparks_rules():
    out = sql.spark_legacy_times(spark("to_date('2020-01-01')"))
    assert (
        value(f"SELECT {sql.duckdb_sql(out)}").isoformat()
        == "2020-01-01"
    )


def test_unix_timestamp_reads_quoted_text_in_its_pattern():
    assert one("SELECT unix_timestamp('2020T01', \"yyyy'T'MM\")") == 1577836800


def test_spark_name_of_a_minus_inside_a_lambda_and_inside_a_sum():
    assert (
        sql.spark_name(spark("transform(array(1), x -> -x)"))
        == "transform(array(1), x -> -x)"
    )
    assert sql.spark_name(spark("-a + 1")) == "((- a) + 1)"
    assert sql.spark_name(spark("-a")) == "(- a)"


def test_hashes_are_refused_by_name_and_other_functions_pass():
    assert sql.refuse_spark_hashes(spark("upper(foo(x))")) is None
    with pytest.raises(ValueError, match=r"^xxhash64\(\) can't run on this PC"):
        sql.refuse_spark_hashes(spark("xxhash64(x)"))


def test_not_null_in_a_struct_type_is_dropped():
    assert rows("SELECT CAST(NULL AS STRUCT<a: INT NOT NULL>) AS s") == [(None,)]


def test_an_array_slice_by_bounds_is_left_as_written():
    assert one("SELECT array(1, 2, 3)[1:2]") == [1, 2]


def test_head_of_a_statement_with_no_rows_counts_nothing():
    table, total = sql.head("CREATE TEMP VIEW v AS SELECT 1")
    assert total == 0


def substring_fixes(text: str) -> str:
    return sql.duckdb_sql(sql.spark_substring(spark(text)))


def test_ignore_nulls_on_first_is_a_filter_and_on_max_is_left_to_refuse():
    assert (
        substring_fixes("SELECT first(x) IGNORE NULLS FROM t")
        == "SELECT FIRST(x) FILTER(WHERE NOT x IS NULL) FROM t"
    )
    with pytest.raises(ValueError, match="IGNORE NULLS"):
        substring_fixes("SELECT max(x) IGNORE NULLS FROM t")


def test_not_null_on_a_table_column_is_kept():
    assert (
        substring_fixes("CREATE TABLE t (a INT NOT NULL)")
        == "CREATE TABLE t (a INT NOT NULL)"
    )


def test_slices_by_bounds_and_substrings_without_a_start_are_left_as_written():
    assert substring_fixes("SELECT a[1:2] FROM t") == "SELECT a[1:2] FROM t"
    assert (
        substring_fixes("SELECT substring('abc') FROM t")
        == "SELECT SUBSTRING('abc') FROM t"
    )
    assert (
        substring_fixes("SELECT slice(a, 2, 1) FROM t")
        == "SELECT ARRAY_SLICE(a, 2, 2) FROM t"
    )


def test_current_catalog_as_the_whole_expression_is_spark_catalog():
    assert (
        sql.duckdb_sql(sql._spark_session_functions(spark("current_catalog()")))
        == "'spark_catalog'"
    )


def test_timestampdiff_as_the_whole_expression_and_with_a_unit_it_doesnt_count():
    whole = sql.duckdb_sql(sql._spark_timestampdiff(spark("timestampdiff(DAY, a, b)")))
    assert (
        value(
            f"SELECT {whole} FROM (SELECT DATE '2020-01-01' a, DATE '2020-02-01' b)"
        )
        == 31
    )
    assert (
        sql.duckdb_sql(
            sql._spark_timestampdiff(spark("timestampdiff(DAYOFWEEK, a, b)"))
        )
        == "DATE_DIFF('DAYOFWEEK', a, b)"
    )


def test_cast_of_a_whole_number_function_past_tinyint_wraps():
    assert one("SELECT CAST(abs(300) AS TINYINT)") == 44
    assert one("SELECT CAST(CAST(3 AS SMALLINT) AS BIGINT)") == 3


def test_time_travel_as_of_a_version_on_a_table_function_reads_it():
    assert rows("SELECT * FROM range(1) VERSION AS OF 1") == [(0,)]


def test_a_date_cast_as_the_whole_expression_reads_text_by_sparks_rules():
    out = sql.spark_legacy_times(
        sql.spark_legacy_casts(spark("CAST('24-02-29' AS DATE)"))
    )
    assert value(f"SELECT {sql.duckdb_sql(out)}") is None
    out = sql.spark_legacy_times(
        sql.spark_legacy_casts(spark("CAST('2024-02-29' AS DATE)"))
    )
    assert (
        value(f"SELECT {sql.duckdb_sql(out)}").isoformat()
        == "2024-02-29"
    )


def test_to_timestamp_with_a_pattern_as_the_whole_expression_is_null_for_text_that_doesnt_match():
    out = sql.duckdb_sql(sql.spark_legacy_times(spark("to_timestamp(a, 'yyyy')")))
    assert "TRY_STRPTIME" in out
    assert value(f"SELECT {out} FROM (SELECT 'nope' a)") is None

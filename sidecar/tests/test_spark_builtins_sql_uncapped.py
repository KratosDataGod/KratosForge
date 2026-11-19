"""Spark SQL built-ins written as DuckDB SQL (spark_builtins_sql): what the uncapped mutation run (batch 187,
docs/quality/mutation-testing.md) showed no test checked. Each expected value is what Apache Spark 3.5 gives."""

from __future__ import annotations

import datetime
import math

import pytest

from fabric_desktop import sql


def one(query: str):
    value = next(iter(sql.run(query).to_pylist()[0].values()))
    if isinstance(value, datetime.datetime) and value.tzinfo is not None:  # Spark's timestamp, in the session's UTC
        return value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value


def written(query: str) -> str:
    return sql.duckdb_sql(sql.parse(query)[0])


def test_base64_breaks_a_long_line_after_76_characters_as_spark_3_5_does():
    text = one("SELECT base64(repeat('a', 60))")
    first, rest = text.split("\r\n")
    assert len(first) == 76 and first + rest == "YWFh" * 20


@pytest.mark.xfail(strict=True, reason=(
    "Bug: sha2 with a bit length written as a decimal, sha2('abc', 256.0), is NULL here; Spark casts the "
    "length to an int (sha2 takes an int, and a decimal casts to one implicitly) and gives the SHA-256. "
    "_sha2 reads a number literal's digits itself and takes 256.0 for a length it doesn't know."))
def test_sha2_with_a_decimal_bit_length_hashes_as_spark_does():
    assert one("SELECT sha2('abc', 256.0)") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_sha2_with_a_bit_length_from_the_data():
    assert one("SELECT sha2('abc', n) FROM VALUES (256) t(n)") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    assert one("SELECT sha2('abc', n) FROM VALUES (1) t(n)") is None


def test_monotonically_increasing_id_numbers_the_rows_of_a_select_from_0():
    assert [r["v"] for r in sql.run("SELECT monotonically_increasing_id() AS v FROM VALUES (1), (2) t(x)")
            .to_pylist()] == [0, 1]


def test_a_call_that_isn_t_a_column_is_read_once_when_cast_to_text():
    # Each level writes its argument once, so nested calls grow by a fixed amount, not threefold per level
    assert len(written("SELECT length(length(length(length(x + 1)))) FROM t")) < 40000
    assert one("SELECT length(length(length(length(x + 1)))) FROM VALUES (12344) t(x)") == 1


def test_java_value_text_writes_a_value_as_spark_does():
    import duckdb

    from fabric_desktop.spark_builtins_sql import java_value_text
    con = duckdb.connect()
    got = con.execute(f"SELECT {java_value_text('x')} FROM (SELECT CAST(1e20 AS DOUBLE) AS x)").fetchone()[0]
    assert got == "1.0E20"


def test_a_column_cast_to_text_is_written_out_so_text_passes_through_without_a_lambda():
    assert written("SELECT upper(x) FROM t").count("TYPEOF(x) = 'VARCHAR' THEN CAST(x AS TEXT)") == 1


def test_spark_text_arg_sql_writes_a_dataframe_argument_as_spark_casts_it():
    import duckdb

    from fabric_desktop.spark_builtins_sql import spark_text_arg_sql
    con = duckdb.connect()
    for a in ("x", "x + 1"):
        got = con.execute(f"SELECT upper({spark_text_arg_sql(a, 'upper')}) FROM (SELECT 1.5 :: DOUBLE AS x)")
        assert got.fetchone()[0] == ("1.5" if a == "x" else "2.5")


def test_initcap_of_a_rewritten_argument_and_of_a_title_case_letter():
    assert one("SELECT initcap(upper('aaa bb'))") == "Aaa Bb"
    assert one("SELECT initcap(x) FROM VALUES ('ǆa b') t(x)") == "ǅa B"


def test_array_insert_pads_up_to_ten_million_nulls_and_refuses_one_more():
    assert one("SELECT size(array_insert(array(1), 10000002, 2))") == 10_000_002
    with pytest.raises(Exception, match="more than 10,000,000 NULLs"):
        one("SELECT array_insert(array(1), 10000003, 2)")
    with pytest.raises(Exception, match="more than 10,000,000 NULLs"):
        one("SELECT array_insert(array(1), -10000003, 2)")
    assert one("SELECT array_insert(array(1), CAST(NULL AS INT), 3)") is None
    with pytest.raises(ValueError, match="needs an INT position"):
        one("SELECT array_insert(array(1, 2), NULL, 3)")


def test_locate_finds_empty_text_at_1_from_a_start_of_1_or_more():
    assert [one(f"SELECT locate('', 'abc', {p})") for p in (2, 1, 0)] == [1, 1, 0]
    assert one("SELECT locate('b', 'abc', 3)") == 0
    assert one("SELECT locate('c', 'abc', 2)") == 3


def test_concat_ws_with_only_a_separator():
    assert one("SELECT concat_ws(',')") == ""
    assert one("SELECT concat_ws(NULL)") is None
    assert one("SELECT concat_ws(',', 'a', 'b')") == "a,b"


def test_levenshtein_counts_characters_and_gives_minus_1_past_the_threshold():
    assert one("SELECT levenshtein('kitten', 'sitting')") == 3
    assert one("SELECT levenshtein('kitten', 'sitting', 2)") == -1
    assert one("SELECT levenshtein('kitten', 'sitting', 3)") == 3
    assert one("SELECT levenshtein(x, y) FROM VALUES ('éa', 'ab') t(x, y)") == 2


def test_upper_and_lower_as_java_writes_them():
    assert one("SELECT upper('straße')") == "STRASSE"
    assert one("SELECT lower('ΟΔΟΣ İ')") == "οδος i̇"
    assert one("SELECT lower(x) FROM VALUES ('AB') t(x)") == "ab"


def test_array_join_skips_or_replaces_nulls():
    assert one("SELECT array_join(array(1, NULL, 3), ',')") == "1,3"
    assert one("SELECT array_join(array(1, NULL), ',', 'x')") == "1,x"
    assert one("SELECT array_join(array(CAST(NULL AS INT)), ',')") == ""
    assert one("SELECT array_join(array(1), CAST(NULL AS STRING))") is None


def test_array_join_with_a_replacement_writes_elements_as_spark_does():
    assert one("SELECT array_join(array(1E20D, NULL), ',', 'x')") == "1.0E20,x"
    assert one("SELECT array_join(array(1, NULL), ',', CAST(NULL AS STRING))") is None


def test_shiftleft_and_shiftright_as_java_shifts_an_int_or_a_long():
    assert one("SELECT shiftleft(1, 31)") == -2 ** 31
    assert one("SELECT shiftleft(-1, 1)") == -2
    assert one("SELECT shiftright(-8, 1)") == -4
    assert one("SELECT shiftleft(3000000000, 1)") == 6_000_000_000
    assert one("SELECT shiftleft(x, 33) FROM VALUES (1) t(x)") == 2  # an int shifts by 33 & 31
    assert one("SELECT shiftleft(x, 33) FROM VALUES (CAST(1 AS BIGINT)) t(x)") == 2 ** 33


@pytest.mark.xfail(strict=True, reason=(
    "Bug: shiftleft of an int column is a bigint here; Spark's shiftleft of an int is an int. _shift writes "
    "a CASE over the value's type when the value isn't an int literal, and its int and long branches make "
    "the result a BIGINT. The same holds for the int literal -2147483648, which reaches _shift already "
    "rewritten (it isn't an int literal there)."))
def test_shiftleft_of_an_int_column_is_an_int():
    assert str(sql.run("SELECT shiftleft(x, 1) AS v FROM VALUES (1) t(x)").schema.field("v").type) == "int32"


def test_octet_length_counts_the_bytes_of_a_value_as_text():
    assert one("SELECT octet_length(x) FROM VALUES (123) t(x)") == 3
    assert one("SELECT octet_length(x) FROM VALUES ('é') t(x)") == 2
    assert one("SELECT octet_length(X'0102')") == 2


def _databricks_rows(query: str) -> list:
    import duckdb

    from fabric_desktop.engines.local_sql import _databricks
    return duckdb.connect().sql(sql.duckdb_sql(_databricks(query)[-1])).fetchall()


def test_databricks_sql_to_date_stops_on_text_that_isn_t_a_date():
    import duckdb
    assert _databricks_rows("SELECT to_date('2026-03-02')") == [(datetime.date(2026, 3, 2),)]
    with pytest.raises(duckdb.Error, match="CAST_INVALID_INPUT"):
        _databricks_rows("SELECT to_date('not a date')")


def test_to_date_with_a_format_reads_the_text_with_it():
    assert one("SELECT to_date('02/03/2026', 'dd/MM/yyyy')") == datetime.date(2026, 3, 2)
    assert one("SELECT to_date('2026-03-02', 'yyyy-MM-dd HH:mm:ss')") is None
    assert one("SELECT to_date('03-02', 'MM-dd')") == datetime.date(1970, 3, 2)  # Spark's year without one
    assert one("SELECT to_date('70-01-02', 'yy-MM-dd')") == datetime.date(2070, 1, 2)


def test_sha2_with_a_bit_length_written_out():
    sha256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert one("SELECT sha2('abc', 256)") == sha256 and one("SELECT sha2('abc', 0)") == sha256
    assert one("SELECT sha2('abc', 224)") == "23097d223405d8228642a477bda255b32aadbce4bda0b3f7e36c9da7"
    assert one("SELECT sha2('abc', 384)") == (
        "cb00753f45a35e8bb5a03d699ac65007272c32ab0eded1631a8b605a43ff5bed8086072ba1e7cc2358baeca134c825a7")
    assert one("SELECT sha2('abc', 512)") == (
        "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f")
    assert one("SELECT sha2('abc', 1)") is None


def test_typeof_names_a_struct_or_map_from_its_type_in_spark_sql():
    from fabric_desktop import spark_local
    spark_local._alias_pyspark()
    spark = spark_local.session()
    assert spark.sql("SELECT typeof(named_struct('a', 1)) AS t").collect()[0]["t"] == "struct<a:int>"
    assert spark.sql("SELECT typeof(map('a', 1)) AS t").collect()[0]["t"] == "map<string,int>"


def test_substring_index_with_a_delimiter_from_the_data_finds_overlapping_ones():
    assert one("SELECT substring_index('xaaaay', d, 2) FROM VALUES ('aa') t(d)") == "xa"
    assert one("SELECT substring_index('xaaay', d, -1) FROM VALUES ('aa') t(d)") == "y"
    assert one("SELECT substring_index('xaaaay', 'aa', 2)") == "xa"
    assert one("SELECT substring_index('a,b,c', ',', 2)") == "a,b"


def test_java_value_text_keeps_duckdb_s_text_of_an_array_and_array_join_refuses_one():
    import duckdb

    from fabric_desktop.spark_builtins_sql import java_value_text
    got = duckdb.connect().execute(f"SELECT {java_value_text('x')} FROM (SELECT [1, 2] AS x)").fetchone()[0]
    assert got == "[1, 2]"
    with pytest.raises(Exception, match="array, map or struct isn't written as text"):
        one("SELECT array_join(array(array(1)), ',')")


def test_text_passes_through_an_outer_text_function_without_another_cast():
    # A Spark cast to string, and upper's result, are text already: the outer call reads them as they are
    assert len(written("SELECT trim(CAST(x AS STRING)) FROM t")) < len(written("SELECT trim(CAST(x AS INT)) FROM t"))
    assert written("SELECT lower(upper(x)) FROM t").count("TYPEOF(") == written("SELECT upper(x) FROM t").count("TYPEOF(")


def test_locate_of_empty_text_from_the_data():
    assert one("SELECT locate(s, 'abc', 2) FROM VALUES ('') t(s)") == 1


def test_contains_with_and_without_a_bare_null():
    assert one("SELECT contains('abc', 'b')") is True
    assert one("SELECT contains(NULL, 'a')") is None


def test_split_refuses_a_pattern_that_matches_empty_text():
    with pytest.raises(ValueError, match="can match empty text"):
        one("SELECT split('abc', 'x*')")
    assert one("SELECT split('a1b', '[0-9]')") == ["a", "b"]


def test_format_string_refuses_a_percent_that_starts_no_conversion():
    for fmt in ("100%", "a %d %"):
        with pytest.raises(ValueError, match="starts no conversion"):
            one(f"SELECT format_string('{fmt}', 1)")


def test_json_tuple_refuses_a_bare_null():
    with pytest.raises(ValueError, match="must all be strings"):
        one("SELECT * FROM (SELECT json_tuple(NULL, 'a'))")


def test_databricks_sql_to_timestamp_ntz_with_a_format_stops_on_text_that_isn_t_one():
    import duckdb
    with pytest.raises(duckdb.Error, match="Could not parse"):
        _databricks_rows("SELECT to_timestamp_ntz('x', 'yyyy-MM-dd')")
    assert one("SELECT to_timestamp_ntz('x', 'yyyy-MM-dd')") is None
    assert one("SELECT try_to_timestamp('x', 'yyyy-MM-dd')") is None


def test_shiftleft_casts_its_value_as_spark_and_keeps_an_int_literal_an_int():
    assert one("SELECT shiftleft(x, 0) FROM VALUES (3e9D) t(x)") == 2_147_483_647  # Java's (int) of a double
    assert one("SELECT shiftleft(1.5, 1)") == 2
    for q in ("SELECT shiftleft(1, 1) AS v", "SELECT shiftleft(-1, 1) AS v"):
        assert str(sql.run(q).schema.field("v").type) == "int32"


def test_extract_from_a_column_of_text_or_an_interval():
    assert one("SELECT EXTRACT(HOUR FROM s) FROM VALUES ('2026-01-01 10:20:00') t(s)") == 10
    got = sql.run("SELECT EXTRACT(DAY FROM i) AS v FROM (SELECT INTERVAL '3' DAY AS i)")
    assert got.to_pylist() == [{"v": 3}] and str(got.schema.field("v").type) == "int32"


def test_any_is_an_aggregate_in_a_select_list_and_in_and_or():
    assert one("SELECT any(b) FROM VALUES (true), (false) t(b)") is True
    assert one("SELECT any(b) AND true FROM VALUES (false) t(b)") is False
    assert one("SELECT 1 = ANY (SELECT 1)") is True


def test_a_dataframe_expression_is_spark_with_ansi_off():
    import duckdb

    from fabric_desktop.spark_compat import spark_expr
    read = spark_expr("to_date('not a date')")
    assert duckdb.connect().execute(f"SELECT {read}").fetchone() == (None,)


def test_monotonically_increasing_id_in_order_by_is_refused_by_name():
    with pytest.raises(ValueError, match="monotonically_increasing_id"):
        one("SELECT x FROM VALUES (1) t(x) ORDER BY monotonically_increasing_id()")


def test_elt_with_one_value():
    assert one("SELECT elt(1, 'a')") == "a"
    assert one("SELECT elt(2, 'a')") is None


def test_make_timestamp_with_a_zone_reads_the_wall_time_there():
    assert one("SELECT make_timestamp(2026, 1, 1, 0, 0, 0, 'Europe/Paris')") == datetime.datetime(2025, 12, 31, 23, 0)
    assert one("SELECT make_timestamp(2026, 1, 1, 0, 0, 0, z) FROM VALUES ('Europe/Paris') t(z)") == (
        datetime.datetime(2025, 12, 31, 23, 0))


def test_raise_error_url_encode_and_url_decode():
    with pytest.raises(Exception, match="boom"):
        one("SELECT raise_error('boom')")
    with pytest.raises(Exception, match="Invalid Input Error: null"):
        one("SELECT raise_error(CAST(NULL AS STRING))")
    assert one("SELECT url_encode('a b*~')") == "a+b*%7E"
    assert one("SELECT url_decode('a%2Bb+c')") == "a+b c"


def test_shiftleft_of_the_int_and_long_literals_next_to_the_int_range():
    got = sql.run("SELECT shiftleft(-2147483648, 1) AS a, shiftleft(2147483648, 1) AS b")
    assert got.to_pylist() == [{"a": 0, "b": 4_294_967_296}]


def test_lateral_view_explode_keeps_an_array_or_struct_element_whole():
    assert sql.run("SELECT a FROM VALUES (1) t(x) LATERAL VIEW explode(array(array(1, 2))) s AS a").to_pylist() == [
        {"a": [1, 2]}]
    assert one("SELECT a FROM VALUES (1) t(x) LATERAL VIEW explode(array(named_struct('k', 1))) s AS a") == {"k": 1}
    assert [r["a"] for r in sql.run("SELECT a FROM VALUES (1) t(x) LATERAL VIEW stack(2, 'p', 'q') s AS a")
            .to_pylist()] == ["p", "q"]


def test_split_with_a_pattern_python_can_t_read_is_left_to_the_engine():
    assert one(r"SELECT split('aXb', '\\p{Lu}')") == ["a", "b"]


def test_levenshtein_writes_two_columns_out_without_binding_them():
    assert "kf_once" not in written("SELECT levenshtein(x, y) FROM t")
    assert "kf_once" in written("SELECT levenshtein(x || 'a', y) FROM t")


def test_a_column_named_window_is_the_column_when_the_query_doesn_t_group_by_window():
    got = sql.run("SELECT window(ts, '10 minutes') AS w, window FROM VALUES (TIMESTAMP '2026-01-01 00:07:00', 5) "
                  "t(ts, window)").to_pylist()
    assert got[0]["window"] == 5
    assert got[0]["w"]["start"] == datetime.datetime(2026, 1, 1, 0, 0)


def test_unbase64_unhex_isnan_and_sqrt_as_spark():
    assert one("SELECT unbase64('YWJj\r\nZGVm')") == b"abcdef"
    assert one("SELECT unbase64('YWJ')") == b"ab"
    assert sql.run("SELECT unhex('4') AS a, unhex('zz') AS b").to_pylist() == [{"a": b"\x04", "b": None}]
    assert sql.run("SELECT isnan(NULL) AS a, isnan(CAST('NaN' AS DOUBLE)) AS b").to_pylist() == [{"a": False, "b": True}]
    assert math.isnan(one("SELECT sqrt(-1)"))


def test_from_csv_of_a_column_and_btrim_with_and_without_characters():
    assert one("SELECT from_csv(s, 'a INT, b STRING') FROM VALUES ('1,x') t(s)") == {"a": 1, "b": "x"}
    assert sql.run("SELECT btrim('xxaxx', 'x') AS a, btrim('  a ') AS b").to_pylist() == [{"a": "a", "b": "a"}]


def test_to_timestamp_ntz_reads_a_column_with_a_format():
    assert one("SELECT to_timestamp_ntz(s, 'dd/MM/yyyy') FROM VALUES ('02/03/2026') t(s)") == (
        datetime.datetime(2026, 3, 2, 0, 0))


def test_format_string_refuses_an_array_for_s():
    with pytest.raises(Exception, match="array, map or struct isn't written as text"):
        one("SELECT format_string('%s', array(1, 2))")


def test_make_timestamp_reads_an_offset_as_its_zone():
    assert one("SELECT make_timestamp(2026, 1, 1, 0, 0, 0, '+01:00')") == datetime.datetime(2025, 12, 31, 23, 0)


def test_a_lambda_s_tuple_names_its_fields_after_the_columns_then_col1_col2():
    assert one("SELECT transform(array(1), x -> (x + 1, x)) AS v") == [{"col1": 2, "x": 1}]
    assert one("SELECT transform(array(1), X -> (X, 1)) AS v") == [{"X": 1, "col2": 1}]


def test_shift_of_a_long_literal_just_below_the_int_range():
    assert one("SELECT shiftleft(-2147483649, 1)") == -4_294_967_298
    assert one("SELECT shiftright(-3000000000, 1)") == -1_500_000_000

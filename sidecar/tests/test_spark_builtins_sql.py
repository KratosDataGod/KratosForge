"""Spark SQL built-ins that DuckDB lacks or answers differently, as spark_builtins_sql writes them
(the Fails and Differs rows of docs/spark-functions.md). Each expected value is what Apache Spark 3.5
gives (its documented example, as captured for spark_builtins.json, or Spark's rules)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.engines.local_sql import _databricks


def rows(query: str) -> list[tuple]:
    return [tuple(r.values()) for r in sql.run(query).to_pylist()]


def one(query: str):
    return rows(query)[0][0]


# (Spark SQL, Spark 3.5's value)
SCALARS = [
    ("SELECT hour('2009-07-30 12:58:59')", 12),
    ("SELECT minute('2009-07-30 12:58:59')", 58),
    ("SELECT second('2009-07-30 12:58:59')", 59),
    ("SELECT quarter('2016-08-31')", 3),
    ("SELECT weekday('2009-07-30')", 3),
    ("SELECT hour('not a time')", None),
    ("SELECT date_trunc('YEAR', '2015-03-05T09:32:05.359')", dt.datetime(2015, 1, 1)),
    ("SELECT trunc('2019-08-04', 'week')", dt.date(2019, 7, 29)),
    ("SELECT date_from_unix_date(1)", dt.date(1970, 1, 2)),
    ("SELECT from_utc_timestamp('2016-08-31', 'Asia/Seoul')", dt.datetime(2016, 8, 31, 9, 0)),
    ("SELECT to_utc_timestamp('2016-08-31', 'Asia/Seoul')", dt.datetime(2016, 8, 30, 15, 0)),
    ("SELECT timestamp_seconds(1230219000)", dt.datetime(2008, 12, 25, 15, 30)),
    ("SELECT timestamp_millis(1230219000123)", dt.datetime(2008, 12, 25, 15, 30, 0, 123000)),
    ("SELECT timestamp_micros(1230219000123123)", dt.datetime(2008, 12, 25, 15, 30, 0, 123123)),
    ("SELECT to_timestamp_ntz('2016-12-31 00:12:00')", dt.datetime(2016, 12, 31, 0, 12)),
    ("SELECT try_to_timestamp('2016-12-31 00:12:00')", dt.datetime(2016, 12, 31, 0, 12)),
    ("SELECT try_to_timestamp('nope')", None),
    ("SELECT try_to_timestamp('2016-12-31', 'yyyy-MM-dd')", dt.datetime(2016, 12, 31)),
    ("SELECT make_timestamp_ntz(2014, 12, 28, 6, 30, 45.887)", dt.datetime(2014, 12, 28, 6, 30, 45, 887000)),
    ("SELECT make_timestamp_ntz(2014, 13, 28, 6, 30, 45)", None),  # Spark with ANSI off: NULL, not an error
    ("SELECT CAST(make_dt_interval(1, 12, 30, 01.001001) AS STRING)", "1 day 12:30:01.001001"),
    ("SELECT isnull(1)", False),
    ("SELECT isnotnull(1)", True),
    ("SELECT nanvl(cast('NaN' as double), 123)", 123.0),
    ("SELECT negative(1)", -1),
    ("SELECT positive(1)", 1),
    ("SELECT e()", 2.718281828459045),
    ("SELECT expm1(0)", 0.0),
    ("SELECT log1p(0)", 0.0),
    ("SELECT log1p(-1)", None),
    ("SELECT hypot(3, 4)", 5.0),
    ("SELECT csc(1)", 1 / 0.8414709848078965),
    ("SELECT sec(0)", 1.0),
    ("SELECT rint(12.3456)", 12.0),
    ("SELECT rint(2.5)", 2.0),
    ("SELECT sign(40)", 1.0),
    ("SELECT signum(-4)", -1.0),
    ("SELECT bround(2.5, 0)", Decimal("2")),
    ("SELECT bround(3.5)", Decimal("4")),
    ("SELECT bround(-2.5)", Decimal("-2")),
    ("SELECT bround(25, -1)", 20),
    ("SELECT bround(0.125, 2)", Decimal("0.12")),
    ("SELECT width_bucket(5.3, 0.2, 10.6, 5)", 3),
    ("SELECT width_bucket(-2.1, 1.3, 3.4, 3)", 0),
    ("SELECT width_bucket(8.1, 0.0, 5.7, 4)", 5),
    ("SELECT width_bucket(-0.9, 5.2, 0.5, 2)", 3),
    ("SELECT try_add(1, 2)", 3),
    ("SELECT try_add(2147483647, 1)", None),
    ("SELECT try_subtract(2, 1)", 1),
    ("SELECT try_multiply(2, 3)", 6),
    ("SELECT try_multiply(2147483647, 2)", None),
    ("SELECT bit_get(11, 0)", 1),
    ("SELECT getbit(11, 2)", 0),
    ("SELECT shiftrightunsigned(4, 1)", 2),
    ("SELECT shiftrightunsigned(-4, 1)", 2147483646),
    ("SELECT shiftrightunsigned(-4L, 1)", 9223372036854775806),
    ("SELECT crc32('Spark')", 1557323817),
    ("SELECT crc32(unhex('00FF'))", 1826356594),
    ("SELECT btrim(' SparkSQL ')", "SparkSQL"),
    ("SELECT elt(1, 'scala', 'java')", "scala"),
    ("SELECT elt(3, 'scala', 'java')", None),
    ("SELECT find_in_set('ab','abc,b,ab,c,def')", 3),
    ("SELECT find_in_set('a,b','a,b')", 0),
    ("SELECT luhn_check('8112189876')", True),
    ("SELECT luhn_check('79927398714')", False),
    ("SELECT luhn_check('8112189a76')", False),
    ("SELECT mask('abcd-EFGH-8765-4321')", "xxxx-XXXX-nnnn-nnnn"),
    ("SELECT mask('abcd-EFGH-8765-4321', 'Q')", "xxxx-QQQQ-nnnn-nnnn"),
    ("SELECT mask('AbCD123-@$#', 'Q', 'q', 'd', 'o')", "QqQQdddoooo"),
    ("SELECT mask('AbCD123-@$#', NULL, 'q', 'd', 'o')", "AqCDdddoooo"),
    ("SELECT octet_length('Spark SQL')", 9),
    ("SELECT octet_length('é')", 2),
    ("SELECT overlay('Spark SQL' PLACING '_' FROM 6)", "Spark_SQL"),
    ("SELECT overlay('Spark SQL' PLACING 'CORE' FROM 7)", "Spark CORE"),
    ("SELECT overlay('Spark SQL' PLACING 'ANSI ' FROM 7 FOR 0)", "Spark ANSI SQL"),
    ("SELECT overlay('Spark SQL' PLACING 'tructured' FROM 2 FOR 4)", "Structured SQL"),
    ("SELECT regexp_substr('Steven Jones and Stephen Smith are the best players', 'Ste(v|ph)en')", "Steven"),
    ("SELECT regexp_substr('Mary had a little lamb', 'Ste(v|ph)en')", None),
    ("SELECT substring_index('www.apache.org', '.', 2)", "www.apache"),
    ("SELECT substring_index('www.apache.org', '.', -2)", "apache.org"),
    ("SELECT substring_index('www.apache.org', '.', 0)", ""),
    ("SELECT substring_index('www', '.', 5)", "www"),
    ("SELECT format_string('Hello World %d %s', 100, 'days')", "Hello World 100 days"),
    ("SELECT printf('%05.2f', 3.14159)", "03.14"),
    ("SELECT typeof(1)", "int"),
    ("SELECT typeof(1L)", "bigint"),
    ("SELECT typeof('a')", "string"),
    ("SELECT typeof(1.5)", "decimal(2,1)"),
    ("SELECT typeof(array(1))", "array<int>"),
    ("SELECT typeof(cast(1 as double))", "double"),
    ("SELECT cardinality(array('b', 'd', 'c', 'a'))", 4),
    ("SELECT cardinality(map(1, 'a'))", 1),
    ("SELECT array_repeat('123', 2)", ["123", "123"]),
    ("SELECT array_repeat('123', -1)", []),
    ("SELECT array_remove(array(1, 2, 3, null, 3), 3)", [1, 2, None]),
    ("SELECT array_remove(array(1, 2), null)", None),
    ("SELECT get(array(1, 2, 3), 0)", 1),
    ("SELECT get(array(1, 2, 3), 3)", None),
    ("SELECT get(array(1, 2, 3), -1)", None),
    ("SELECT arrays_zip(array(1, 2, 3), array(2, 3))", [{"0": 1, "1": 2}, {"0": 2, "1": 3}, {"0": 3, "1": None}]),
    ("SELECT zip_with(array(1, 2), array('a', 'b'), (x, y) -> (y, x))", [{"y": "a", "x": 1}, {"y": "b", "x": 2}]),
    ("SELECT map_zip_with(map(1, 'a', 2, 'b'), map(1, 'x', 2, 'y'), (k, v1, v2) -> concat(v1, v2))", [(1, "ax"), (2, "by")]),
    ("SELECT map_zip_with(map(1, 'a'), map(2, 'y'), (k, v1, v2) -> coalesce(v1, v2))", [(1, "a"), (2, "y")]),
    ("SELECT to_csv(named_struct('a', 1, 'b', 2))", "1,2"),
    ("SELECT to_csv(struct('x,y', '', 'say \"hi\"', ' p ', null))", '"x,y","","say \\"hi\\"",p,'),
    ("SELECT from_csv('1, 0.8', 'a INT, b DOUBLE')", {"a": 1, "b": 0.8}),
    ("SELECT from_csv('1,', 'a INT, b STRING')", {"a": 1, "b": None}),
    ("SELECT try_to_binary('abc', 'utf-8')", b"abc"),
    ("SELECT try_to_binary('zz', 'hex')", None),
    ("SELECT assert_true(0 < 1)", None),
    ("SELECT schema_of_json('[{\"col\":0}]')", "ARRAY<STRUCT<col: BIGINT>>"),
]


@pytest.mark.parametrize("query, want", SCALARS, ids=[q for q, _ in SCALARS])
def test_each_gives_sparks_value(query, want):
    got = one(query)
    if isinstance(want, float):
        assert got == pytest.approx(want)
    else:
        assert got == want


AGGREGATES = [
    ("SELECT any(col) FROM VALUES (true), (false), (false) AS tab(col)", True),
    ("SELECT some(col) FROM VALUES (false), (false) AS tab(col)", False),
    ("SELECT every(col) FROM VALUES (true), (false) AS tab(col)", False),
    ("SELECT std(col) FROM VALUES (1), (2), (3) AS tab(col)", 1.0),
    ("SELECT first_value(col) FROM VALUES (10), (5), (20) AS tab(col)", 10),
    ("SELECT last_value(col) FROM VALUES (10), (5), (20) AS tab(col)", 20),
    ("SELECT try_avg(col) FROM VALUES (1), (2), (3) AS tab(col)", 2.0),
    ("SELECT try_sum(col) FROM VALUES (5), (10), (15) AS tab(col)", 30),
    ("SELECT kurtosis(col) FROM VALUES (-10), (-20), (100), (1000) AS tab(col)", -0.7014368047529627),
    ("SELECT kurtosis(col) FROM VALUES (1), (10), (100), (10), (1) AS tab(col)", 0.19432323191699075),
    ("SELECT skewness(col) FROM VALUES (-10), (-20), (100), (1000) AS tab(col)", 1.1135657469022011),
    ("SELECT skewness(col) FROM VALUES (-1000), (-100), (10), (20) AS tab(col)", -1.1135657469022011),
    ("SELECT skewness(col) FROM VALUES (1), (1), (1) AS tab(col)", None),   # no spread: NULL in Spark
    ("SELECT skewness(col) FROM VALUES (1), (3) AS tab(col)", 0.0),
    ("SELECT approx_percentile(col, array(0.5, 0.4, 0.1), 100) FROM VALUES (0), (1), (2), (10) AS tab(col)", [1, 1, 0]),
    ("SELECT percentile_approx(col, 0.5) FROM VALUES (0), (6), (7), (9), (10) AS tab(col)", 7),
    ("SELECT substring(hex(bitmap_construct_agg(bitmap_bit_position(col))), 0, 6) "
     "FROM VALUES (1), (2), (3) AS tab(col)", "070000"),
]


@pytest.mark.parametrize("query, want", AGGREGATES, ids=[q for q, _ in AGGREGATES])
def test_each_aggregate_gives_sparks_value(query, want):
    got = one(query)
    if isinstance(want, float):
        assert got == pytest.approx(want)
    else:
        assert got == want


def test_rank_ignores_its_argument_as_spark_does():
    q = "SELECT a, b, {f}(b) OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) tab(a, b)"
    assert sorted(rows(q.format(f="rank"))) == [("A1", 1, 1), ("A1", 1, 1), ("A1", 2, 3), ("A2", 3, 1)]
    assert sorted(rows(q.format(f="dense_rank"))) == [("A1", 1, 1), ("A1", 1, 1), ("A1", 2, 2), ("A2", 3, 1)]
    assert sorted(rows(q.format(f="percent_rank"))) == [("A1", 1, 0.0), ("A1", 1, 0.0), ("A1", 2, 1.0), ("A2", 3, 0.0)]


def test_grouping_id_with_no_arguments_covers_every_group_by_column():
    q = ("SELECT name, grouping_id(), sum(age) FROM VALUES (2, 'Alice', 165), (5, 'Bob', 180) people(age, name, height) "
         "GROUP BY cube(name, height)")
    assert sorted(rows(q), key=str) == [("Alice", 0, 2), ("Alice", 1, 2), ("Bob", 0, 5), ("Bob", 1, 5),
                                        (None, 2, 2), (None, 2, 5), (None, 3, 7)]


def test_generators_give_sparks_rows_and_column_names():
    def table(query):
        t = sql.run(query)
        return t.column_names, [tuple(r.values()) for r in t.to_pylist()]
    assert table("SELECT explode_outer(array(10, 20))") == (["col"], [(10,), (20,)])
    assert table("SELECT explode_outer(array())") == (["col"], [(None,)])
    assert table("SELECT posexplode_outer(array(10, 20))") == (["pos", "col"], [(0, 10), (1, 20)])
    assert table("SELECT posexplode_outer(array())") == (["pos", "col"], [(None, None)])
    assert table("SELECT inline(array(struct(1, 'a'), struct(2, 'b')))") == (["col1", "col2"], [(1, "a"), (2, "b")])
    assert table("SELECT inline_outer(array(struct(1, 'a'), struct(2, 'b')))") == (["col1", "col2"], [(1, "a"), (2, "b")])
    assert table("SELECT stack(2, 1, 2, 3)") == (["col0", "col1"], [(1, 2), (3, None)])
    assert table("""SELECT json_tuple('{"a":1, "b":{"c":2}}', 'a', 'b', 'z')""") == (
        ["c0", "c1", "c2"], [("1", '{"c":2}', None)])
    assert table("SELECT json_tuple('not json', 'a')") == (["c0"], [(None,)])


def test_a_selected_call_keeps_the_name_spark_gives_it():
    assert sql.run("SELECT isnull(1), substring_index('a.b', '.', 1)").column_names == [
        "isnull(1)", "substring_index('a.b', '.', 1)"]


@pytest.mark.parametrize("query, match", [
    ("SELECT aes_decrypt(unhex('00'), '0000111122223333')", "AES"),
    ("SELECT xpath('<a><b>b1</b></a>', 'a/b/text()')", "XPath"),
    ("SELECT hll_sketch_estimate(hll_sketch_agg(col)) FROM VALUES (1) tab(col)", "approx_count_distinct"),
    ("SELECT histogram_numeric(col, 5) FROM VALUES (0) AS tab(col)", "width_bucket"),
    ("SELECT count_min_sketch(col, 0.5d, 0.5d, 1) FROM VALUES (1) AS tab(col)", "sketch"),
    ("SELECT make_ym_interval(1, 2)", "year-month"),
    ("SELECT sentences('Hi there! Good morning.')", "locale"),
    ("SELECT try_to_number('454', '999')", "format"),
    ("SELECT to_varchar(454, '999')", "format"),
])
def test_what_cant_give_sparks_value_is_refused_by_name(query, match):
    with pytest.raises(ValueError, match=f"this PC.*{match}|{match}.*this PC"):
        sql.run(query)


@pytest.mark.parametrize("query", [
    "SELECT to_csv(struct(1.5D))",
    "SELECT to_csv(struct(current_timestamp()))",
    "SELECT from_csv('1,\"a\"', 'a INT, b STRING')",
])
def test_values_spark_writes_or_reads_its_own_way_stop_the_query(query):
    with pytest.raises(duckdb.Error, match="this PC"):
        sql.run(query)


def test_assert_true_stops_on_false():
    with pytest.raises(duckdb.Error, match="is not true"):
        sql.run("SELECT assert_true(1 > 2)")


def test_rewrites_only_read_what_the_person_wrote():
    # format_number is written as DuckDB's FORMAT by another rewrite; that FORMAT must not be read
    # as Spark's format_string afterwards.
    assert one("SELECT format_number(12332.123456, 4)") == "12,332.1235"
    # percentile stays the interpolating one; only approx_percentile picks a value from the column.
    assert one("SELECT percentile(col, 0.5) FROM VALUES (0), (1), (2), (10) AS tab(col)") == 1.5


def test_reserved_names_are_refused_plainly():
    with pytest.raises(ValueError, match="kf_"):
        sql.parse("SELECT array_repeat(kf_i, 2) FROM VALUES (1) AS t(kf_i)")


def test_date_trunc_of_a_timestamp_keeps_its_type():
    statement = sql.parse("SELECT date_trunc('HOUR', ts) FROM t")[0]
    assert "TRY_CAST" not in sql.duckdb_sql(statement)


def test_databricks_sql_gets_the_same_functions():
    con = duckdb.connect()
    for query, want in [("SELECT substring_index('www.apache.org', '.', 2)", "www.apache"),
                        ("SELECT hour('2009-07-30 12:58:59')", 12),
                        ("SELECT try_add(2147483647, 1)", None)]:
        assert con.execute(sql.duckdb_sql(_databricks(query)[-1])).fetchone()[0] == want


@pytest.mark.parametrize("fmt, text, want", [
    ("yyyy-MM-dd HH:mm:ss", "2026-03-02 10:11:12", dt.date(2026, 3, 2)),
    ("yyyy-MM-dd HH:mm:ss", "2026-03-02", None),     # the format is read, not dropped: NULL as in Spark
    ("yyyy-MM-dd HH:mm:ss", "2026/03/01", None),
    ("yyyy-MM-dd", "2026/03/02", None),
    ("MM/dd/yyyy", "03/02/2026", dt.date(2026, 3, 2)),
])
def test_to_date_reads_the_format_it_is_given(fmt, text, want):
    assert one(f"SELECT to_date('{text}', '{fmt}')") == want


def test_dataframe_to_date_reads_the_format_too():
    from fabric_desktop import spark_compat
    query = spark_compat.spark_expr("to_date(t, 'yyyy-MM-dd HH:mm:ss')")
    con = duckdb.connect()
    got = con.execute(f"SELECT {query} FROM (VALUES ('2026-03-02'), ('2026-03-02 10:11:12')) AS v(t)").fetchall()
    assert got == [(None,), (dt.date(2026, 3, 2),)]


# ---- binary literals written X '..', bitmaps, and tumbling time windows (Spark 3.5's values)

def test_a_hex_literal_with_a_space_reads_as_spark_reads_it():
    assert rows("SELECT hex(X '1010'), hex(x  'ff'), hex(X'10'), 'X' 'a'") == [("1010", "FF", "10", "Xa")]
    with pytest.raises(ValueError, match="Could not read the query"):
        sql.run("SELECT X 'zz'")  # not hex: the error names the query as written


def test_bitmap_count_and_bitmap_or_agg():
    assert one("SELECT bitmap_count(X '1010')") == 2
    assert rows("SELECT bitmap_count(X''), bitmap_count(NULL)") == [(0, None)]
    assert one("SELECT substring(hex(bitmap_or_agg(col)), 0, 6) FROM VALUES (X '10'), (X '20'), (X '40') AS tab(col)") == "700000"
    # Spark's bitmap is 4096 bytes: shorter ones are ORed into its first bytes, and NULLs are skipped.
    assert rows("SELECT octet_length(bitmap_or_agg(c)), substring(hex(bitmap_or_agg(c)), 1, 4) "
                "FROM VALUES (CAST(NULL AS BINARY)), (X'01'), (X'0102') t(c)") == [(4096, "0102")]
    assert one("SELECT bitmap_count(bitmap_or_agg(m)) FROM (SELECT bitmap_construct_agg(c) m FROM VALUES (1), (2) t(c) "
               "UNION ALL SELECT bitmap_construct_agg(c) FROM VALUES (2), (3) t(c))") == 3


_TIMES = "VALUES ('A1', '2021-01-01 00:00:00'), ('A1', '2021-01-01 00:04:30'), ('A1', '2021-01-01 00:06:00'), ('A2', '2021-01-01 00:01:00') AS tab(a, b)"


def test_window_groups_rows_into_tumbling_windows():
    t = dt.datetime
    assert rows(f"SELECT a, window.start, window.end, count(*) as cnt FROM {_TIMES} "
                "GROUP by a, window(b, '5 minutes') ORDER BY a, start") == [
        ("A1", t(2021, 1, 1, 0, 0), t(2021, 1, 1, 0, 5), 2), ("A1", t(2021, 1, 1, 0, 5), t(2021, 1, 1, 0, 10), 1),
        ("A2", t(2021, 1, 1, 0, 0), t(2021, 1, 1, 0, 5), 1)]
    assert rows(f"SELECT a, window.start as start, window.end as end, window_time(window), cnt FROM (SELECT a, window, "
                f"count(*) as cnt FROM {_TIMES} GROUP by a, window(b, '5 minutes') ORDER BY a, window.start)") == [
        ("A1", t(2021, 1, 1, 0, 0), t(2021, 1, 1, 0, 5), t(2021, 1, 1, 0, 4, 59, 999999), 2),
        ("A1", t(2021, 1, 1, 0, 5), t(2021, 1, 1, 0, 10), t(2021, 1, 1, 0, 9, 59, 999999), 1),
        ("A2", t(2021, 1, 1, 0, 0), t(2021, 1, 1, 0, 5), t(2021, 1, 1, 0, 4, 59, 999999), 1)]
    # window(...) first in GROUP BY, an 'interval ...' duration, and HAVING
    assert rows(f"SELECT window.start, count(*) FROM {_TIMES} GROUP BY window(b, 'interval 5 minutes') "
                "HAVING count(*) > 1") == [(t(2021, 1, 1, 0, 0), 3)]
    # a selected window is a struct named window; rows whose time is NULL or not a time are skipped, as in Spark
    result = sql.run("SELECT window(b, '1 hour') FROM VALUES ('2021-01-01 10:30:00'), (NULL), ('junk') t(b)")
    assert result.column_names == ["window"]
    assert result.to_pylist() == [{"window": {"start": t(2021, 1, 1, 10, 0), "end": t(2021, 1, 1, 11, 0)}}]


@pytest.mark.parametrize("query, match", [
    ("SELECT window(b, '10 minutes', '5 minutes') FROM VALUES ('2021-01-01') t(b)", "sliding"),
    ("SELECT window(b, '1 month') FROM VALUES ('2021-01-01') t(b)", "duration"),
    ("SELECT window(b, d) FROM VALUES ('2021-01-01', '5 minutes') t(b, d)", "duration"),
    ("SELECT session_window(b, '5 minutes') FROM VALUES ('2021-01-01') t(b)", "session windows"),
])
def test_windows_this_pc_cant_match_are_refused(query, match):
    with pytest.raises(ValueError, match=f"this PC.*{match}|{match}.*this PC"):
        sql.run(query)


def test_window_counts_from_1970_utc_in_the_sessions_time_zone():
    from fabric_desktop import spark_local
    spark_local._alias_pyspark()
    spark = spark_local.session()
    zone = spark.conf.get("spark.sql.session.timeZone")
    spark.conf.set("spark.sql.session.timeZone", "America/Los_Angeles")
    try:
        got = spark.sql("SELECT window(b, '1 day') AS w FROM VALUES ('2021-01-01 10:00:00'), ('2021-03-14 10:00:00') t(b)").collect()
    finally:
        spark.conf.set("spark.sql.session.timeZone", zone)
    t = dt.datetime
    # a day's window starts at 00:00 UTC (16:00 the day before in Los Angeles) and lasts 86,400 seconds,
    # also on 14 March 2021, when Los Angeles moved its clocks forward
    assert [tuple(r.w) for r in got] == [(t(2020, 12, 31, 16, 0), t(2021, 1, 1, 16, 0)),
                                        (t(2021, 3, 13, 16, 0), t(2021, 3, 14, 17, 0))]




# parse_url, with the values Apache Spark 3.5 gave (it reads the text with java.net.URI, and a URL
# URI refuses is NULL).
@pytest.mark.parametrize("url, part, want", [
    ("http://spark.apache.org/path?query=1", "HOST", "spark.apache.org"),
    ("http://spark.apache.org/path?query=1", "FILE", "/path?query=1"),
    ("https://user:pw@www.example.com:8080/a/b.html?x=1&y=2&x=3#frag", "AUTHORITY", "user:pw@www.example.com:8080"),
    ("https://user:pw@www.example.com:8080/a/b.html?x=1&y=2&x=3#frag", "USERINFO", "user:pw"),
    ("https://user:pw@www.example.com:8080/a/b.html?x=1&y=2&x=3#frag", "REF", "frag"),
    ("https://user:pw@www.example.com:8080/a/b.html?x=1&y=2&x=3#frag", "PROTOCOL", "https"),
    ("http://host", "PATH", ""),
    ("http://host?q", "FILE", "?q"),
    ("file:///tmp/x.txt", "HOST", None),
    ("file:///tmp/x.txt", "PATH", "/tmp/x.txt"),
    ("mailto:someone@example.com", "PROTOCOL", "mailto"),
    ("mailto:someone@example.com", "PATH", None),
    ("/rel/path?q=1#f", "QUERY", "q=1"),
    ("//host/p?x=1", "HOST", "host"),
    ("http://my_host/p", "HOST", None),           # not a host name: URI reads it as a registry authority
    ("http://my_host/p", "AUTHORITY", "my_host"),
    ("http://999.1.1.1/p", "HOST", None),
    ("http://10.0.0.1:80/p", "HOST", "10.0.0.1"),
    ("http://host:/p", "AUTHORITY", "host:"),
    ("http://user@/p", "USERINFO", None),
    ("http://host/p%20a", "PATH", "/p%20a"),
    ("http://host/é", "PATH", "/é"),
    ("http://h/p?k=v#x?y", "REF", "x?y"),
    ("abfss://c@acct.dfs.core.windows.net/path/x", "USERINFO", "c"),
    ("http://host/p a", "HOST", None),            # URI refuses these, so Spark gives NULL
    ("http://host/p%zz", "PATH", None),
    ("http://host/p[1]", "PATH", None),
    ("http://host/p#a#b", "REF", None),
    ("1http://x", "PATH", None),
    ("http:", "PROTOCOL", None),
    ("http://h/p?x=1", "host", None),             # the part is case-sensitive
])
def test_parse_url_gives_sparks_value(url, part, want):
    assert one(f"SELECT parse_url('{url}', '{part}')") == want


@pytest.mark.parametrize("url, key, want", [
    ("http://h/p?x=1&y=2&x=3", "x", "1"),
    ("http://h/p?ak=1&k=2", "k", "2"),
    ("http://h/p?K=1", "k", None),
    ("http://h/p?k=v=w", "k", "v=w"),
    ("http://h/p?=1&k=", "k", ""),
    ("http://h/p?axb=1", "a.b", "1"),             # Spark reads the key as a pattern; . matches any one
    ("http://h/p?k-1=v&k_2=w", "k-1", "v"),
    ("mailto:x?k=1", "k", None),
])
def test_parse_url_query_key_gives_sparks_value(url, key, want):
    assert one(f"SELECT parse_url('{url}', 'QUERY', '{key}')") == want


def test_parse_url_of_a_column_and_nulls():
    got = rows("SELECT parse_url(u, 'HOST') FROM VALUES ('http://a.com/x'), (NULL), ('http://b.org') AS t(u)")
    assert got == [("a.com",), (None,), ("b.org",)]
    assert one("SELECT parse_url('http://h/p?x=1', NULL)") is None
    assert one("SELECT parse_url('http://h/p?x=1', 'QUERY', NULL)") is None


@pytest.mark.parametrize("query", [
    "SELECT parse_url('http://[::1]:80/p', 'HOST')",
    "SELECT parse_url('http://h/p?a[]=1', 'QUERY', 'a[]')",
])
def test_parse_url_refuses_what_could_differ_by_name(query):
    with pytest.raises(duckdb.Error, match="parse_url.*this PC"):
        sql.run(query)


# EXTRACT and date_part as Spark 3.5 answers them: 2019-08-12 is a Monday, the day of the week counts
# from Sunday = 1 (ISO from Monday = 1), seconds keep their fraction as decimal(8,6), the other fields
# are int, and Spark's TIMESTAMP (read as TIMESTAMPTZ) is read in the session's zone, UTC.
@pytest.mark.parametrize("query, want, kind", [
    ("SELECT extract(YEAR FROM TIMESTAMP '2019-08-12 01:00:00.123456')", 2019, "int"),
    ("SELECT extract(HOUR FROM CAST('2019-08-12 23:00:00' AS TIMESTAMP))", 23, "int"),
    ("SELECT extract(DOW FROM DATE '2019-08-12')", 2, "int"),
    ("SELECT extract(DAYOFWEEK FROM TIMESTAMP '2019-08-18 10:00:00')", 1, "int"),
    ("SELECT extract(DOW_ISO FROM DATE '2019-08-18')", 7, "int"),
    ("SELECT extract(SECONDS FROM TIMESTAMP '2019-10-01 00:00:01.000001')", Decimal("1.000001"), "decimal(8,6)"),
    ("SELECT extract(S FROM TIMESTAMP '2019-10-01 00:00:59.999999')", Decimal("59.999999"), "decimal(8,6)"),
    ("SELECT extract(SECOND FROM DATE '2019-10-01')", Decimal("0"), "decimal(8,6)"),
    ("SELECT extract(WEEK FROM DATE '2019-08-12')", 33, "int"),
    ("SELECT extract(YEAR FROM '2019-08-12')", 2019, "int"),
    ("SELECT extract(YEAR FROM 'not a date')", None, "int"),
    ("SELECT date_part('DOW', DATE '2019-08-12')", 2, "int"),
    ("SELECT datepart('SECONDS', TIMESTAMP '2019-10-01 00:00:01.000001')", Decimal("1.000001"), "decimal(8,6)"),
    ("SELECT date_part('YEAR', TIMESTAMP '2019-10-01 00:00:01')", 2019, "int"),
])
def test_extract_and_date_part_give_sparks_value_and_type(query, want, kind):
    assert one(query) == want
    assert one(f"SELECT typeof(x) FROM ({query}) AS t(x)") == kind  # Spark's typeof


@pytest.mark.parametrize("query, want", [
    ("SELECT to_unix_timestamp('2016-04-08', 'yyyy-MM-dd')", 1460073600),
    ("SELECT to_unix_timestamp('04/08/2016 01:02:03', 'MM/dd/yyyy HH:mm:ss')", 1460077323),
    ("SELECT to_unix_timestamp('2016-04-08 10:11:12')", 1460110272),
    ("SELECT to_unix_timestamp('2016/04/08', 'yyyy-MM-dd')", None),
])
def test_to_unix_timestamp_reads_its_format_as_unix_timestamp_does(query, want):
    assert one(query) == want

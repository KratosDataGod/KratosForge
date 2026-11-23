"""Values of more than one type in one call, max_by and min_by with a NULL value, RANGE frames with an INTERVAL,
and concat_ws of an array column, as Apache Spark 3.5.3 (local[1], ANSI off, UTC) answered each of them.

Spark finds one type for array(), map()'s keys and values, coalesce, CASE, if, IN and concat of arrays: a string
next to a number, a date or a timestamp makes them all strings, and a boolean next to a number has no such type,
so Spark stops ([DATATYPE_MISMATCH]). This PC answered array(true, 1) and true IN (1), and stopped on array(1, 'a')
with DuckDB's conversion error (spark_coercion). Every expected value below was read from Spark 3.5.3, value and
typeof alike.
"""

from __future__ import annotations

import pytest

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark():
    session = Kernel().namespace["spark"]
    session.sql("CREATE OR REPLACE TEMP VIEW kf_mix AS SELECT * FROM VALUES (1, 'a', true, 1.5D, "
                "CAST(2.50 AS DECIMAL(5,2)), DATE'2024-01-01', TIMESTAMP'2024-01-01 10:00:00', array(1, 2), "
                "array('x'), CAST(3 AS BIGINT), array(array('p')), array('p', 'q')) "
                "AS t(i, s, b, d, m, dt, ts, ai, ax, l, aa, sq)")
    return session


def row(spark, query: str) -> tuple:
    return tuple(spark.sql(query).collect()[0])


@pytest.mark.parametrize(("query", "expected"), [
    ("SELECT array(1, 'a'), typeof(array(1, 'a'))", (["1", "a"], "array<string>")),
    ("SELECT map(1, 'a', 'b', 2), typeof(map(1, 'a', 'b', 2))", ({"1": "a", "b": "2"}, "map<string,string>")),
    ("SELECT array(date'2024-01-01', 'x', 1)", (["2024-01-01", "x", "1"],)),
    ("SELECT array(1.50, 'a'), array(CAST(2.5 AS FLOAT), 'a'), array(1L, 'a'), array(1.0D, 'a')",
     (["1.50", "a"], ["2.5", "a"], ["1", "a"], ["1.0", "a"])),
    ("SELECT array(timestamp'2024-01-01 10:00:00', 'a')", (["2024-01-01 10:00:00", "a"],)),
    ("SELECT array(array(1.5D), array('a')), typeof(array(array(1), array('a')))", ([["1.5"], ["a"]], "array<array<string>>")),
    ("SELECT map(1, array(1), 2, array('a'))", ({1: ["1"], 2: ["a"]},)),
    ("SELECT array(1, NULL, 'a'), typeof(array(1, NULL, 'a'))", (["1", None, "a"], "array<string>")),
    ("SELECT coalesce(1, 'a'), typeof(coalesce(1, 'a')), coalesce(1, 1.5D, 'a'), nvl(1, 'a')", ("1", "string", "1", "1")),
    ("SELECT coalesce(CAST(NULL AS INT), 'a'), typeof(coalesce(NULL, 1.5D, 'a'))", ("a", "string")),
    ("SELECT coalesce(array(1), NULL, array('a')), typeof(coalesce(array(1, 2), array('a')))", (["1"], "array<string>")),
    ("SELECT concat(array(1, 2), array('a')), typeof(concat(array(1, 2), array('a')))", (["1", "2", "a"], "array<string>")),
    ("SELECT CASE WHEN true THEN 1 ELSE 'a' END, typeof(CASE WHEN true THEN 1 ELSE 'a' END), typeof(if(true, 1.5D, 'a'))",
     ("1", "string", "string")),
    ("SELECT typeof(array(1, 1.5)), typeof(array(CAST(1 AS BIGINT), 1.5)), typeof(map(1, 1.5, 2, 2))",
     ("array<decimal(11,1)>", "array<decimal(21,1)>", "map<int,decimal(11,1)>")),
    ("SELECT array_intersect(array(1, 2), array(1.0D)), typeof(array_intersect(array(1, 2), array(1.0D)))", ([1.0], "array<double>")),
    ("SELECT array_except(array(1, 2), array(1.0D)), typeof(array_except(array(1, 2), array(1.0D)))", ([2.0], "array<double>")),
    ("SELECT array_remove(array(1, 2), 2.0D), typeof(array_remove(array(1, 2), 2.0D))", ([1.0], "array<double>")),
    ("SELECT typeof(array_union(array(CAST(1 AS DECIMAL(12,1))), array(1)))", ("array<decimal(12,1)>",)),
    ("SELECT array_contains(array(1, 2), 1.0), array_contains(array(1.5), 1.25), array_position(array(1, 2), 2.0D)",
     (True, False, 2)),
    ("SELECT 1 IN ('1'), '1' IN (1), date'2024-01-01' IN ('2024-01-01'), 1.5 IN (1, 2.5D)", (True, True, True, False)),
    ("SELECT greatest(1, 2.5D), typeof(least(1, 1.5))", (2.5, "decimal(11,1)")),
])
def test_one_type_for_mixed_values(spark, query, expected):
    assert row(spark, query) == expected


@pytest.mark.parametrize(("query", "what", "code"), [
    ("SELECT array(true, 1)", r"array\(\)", "DATA_DIFF_TYPES"),
    ("SELECT array(true, 'a')", r"array\(\)", "DATA_DIFF_TYPES"),
    ("SELECT array(1, date'2024-01-01')", r"array\(\)", "DATA_DIFF_TYPES"),
    ("SELECT array(X'01', 'a')", r"array\(\)", "DATA_DIFF_TYPES"),
    ("SELECT true IN (1)", "IN", "DATA_DIFF_TYPES"),
    ("SELECT 'true' IN (true, false)", "IN", "DATA_DIFF_TYPES"),
    ("SELECT NULL IN (true, 1)", "IN", "DATA_DIFF_TYPES"),
    ("SELECT date'2024-01-01' IN (1)", "IN", "DATA_DIFF_TYPES"),
    ("SELECT map(1, true, 2, 1)", r"map\(\)'s values", "CREATE_MAP_VALUE_DIFF_TYPES"),
    ("SELECT map(true, 1, 1, 2)", r"map\(\)'s keys", "CREATE_MAP_KEY_DIFF_TYPES"),
    ("SELECT coalesce(true, 1)", r"coalesce\(\)", "DATA_DIFF_TYPES"),
    ("SELECT coalesce(array(1, 2), array(true))", r"coalesce\(\)", "DATA_DIFF_TYPES"),
    ("SELECT concat(array(1, 2), array(true))", r"concat\(\)", "DATA_DIFF_TYPES"),
    ("SELECT concat(array(1), 'a')", r"concat\(\)", "DATA_DIFF_TYPES"),
    ("SELECT greatest(1, 'a')", r"greatest\(\)", "DATA_DIFF_TYPES"),
    ("SELECT array_contains(array(1, 2), true)", r"array_contains\(\)", "ARRAY_FUNCTION_DIFF_TYPES"),
    ("SELECT array_contains(array('1', '2'), 1)", r"array_contains\(\)", "ARRAY_FUNCTION_DIFF_TYPES"),
    ("SELECT array_position(array(1, 2), 1.5)", r"array_position\(\)", "ARRAY_FUNCTION_DIFF_TYPES"),
    ("SELECT array_remove(array(1, 2), 1.5)", r"array_remove\(\)", "ARRAY_FUNCTION_DIFF_TYPES"),
    ("SELECT array_union(array(1, 2), array(1.5))", r"array_union\(\)", "BINARY_ARRAY_DIFF_TYPES"),
    ("SELECT array_union(array(1.5), array(1.25))", r"array_union\(\)", "BINARY_ARRAY_DIFF_TYPES"),
    ("SELECT array_union(array(1, 2), array(true))", r"array_union\(\)", "BINARY_ARRAY_DIFF_TYPES"),
    ("SELECT arrays_overlap(array(1, 2), array(1.0))", r"arrays_overlap\(\)", "BINARY_ARRAY_DIFF_TYPES"),
    ("SELECT array_except(array(1, 2), array('1'))", r"array_except\(\)", "BINARY_ARRAY_DIFF_TYPES"),
])
def test_values_spark_has_no_one_type_for_are_refused_by_name(spark, query, what, code):
    with pytest.raises(ValueError, match=rf"^{what} can't take .* values together, .*, so nothing ran\. Spark stops on "
                                         rf"it too \(\[DATATYPE_MISMATCH\.{code}\]\)\. Cast the values to one type first"):
        spark.sql(query).collect()


@pytest.mark.parametrize(("query", "expected"), [
    ("SELECT array(i, s), typeof(array(i, s)) FROM kf_mix", (["1", "a"], "array<string>")),
    ("SELECT coalesce(d, s), coalesce(ts, s), typeof(coalesce(i, s)) FROM kf_mix", ("1.5", "2024-01-01 10:00:00", "string")),
    ("SELECT array(dt, s), array(m, s), typeof(array(l, m)) FROM kf_mix", (["2024-01-01", "a"], ["2.50", "a"], "array<decimal(22,2)>")),
    ("SELECT map(i, s, s, i) FROM kf_mix", ({"1": "a", "a": "1"},)),
    ("SELECT concat(ai, ax), coalesce(ai, ax) FROM kf_mix", (["1", "2", "x"], ["1", "2"])),
    ("SELECT array_union(ai, array(d)), array_remove(ai, d) FROM kf_mix", ([1.0, 2.0, 1.5], [1.0, 2.0])),
    ("SELECT CASE WHEN b THEN i ELSE s END, if(b, d, s), CASE i WHEN 1 THEN 'one' ELSE 2 END FROM kf_mix", ("1", "1.5", "one")),
])
def test_columns_take_sparks_one_type(spark, query, expected):
    assert row(spark, query) == expected


@pytest.mark.parametrize("query", [
    "SELECT array(i, b) FROM kf_mix", "SELECT b IN (1) FROM kf_mix", "SELECT i IN (b) FROM kf_mix",
    "SELECT array_contains(ai, s) FROM kf_mix", "SELECT array_union(ai, array(m)) FROM kf_mix",
    "SELECT CASE WHEN b THEN i ELSE b END FROM kf_mix", "SELECT greatest(i, b) FROM kf_mix",
    "SELECT array_position(ai, m) FROM kf_mix",
])
def test_columns_spark_has_no_one_type_for_are_refused(spark, query):
    with pytest.raises(ValueError, match=r"so nothing ran\. Spark stops on it too \(\[DATATYPE_MISMATCH\."):
        spark.sql(query).collect()


def test_sql_run_takes_the_same_types():
    """sql.run (a %%sql cell's path) types the values as spark.sql does."""
    assert sql.run("SELECT array(1, 'a') AS a, coalesce(1, 'a') AS c").to_pylist() == [{"a": ["1", "a"], "c": "1"}]
    with pytest.raises(ValueError, match=r"^IN can't take boolean and int values together"):
        sql.run("SELECT true IN (1)")


def _size(query: str) -> int:
    from fabric_desktop.sql import duckdb_sql, parse
    from fabric_desktop.spark_coercion import spark_coercions
    from sqlglot.schema import MappingSchema
    schema = MappingSchema({"t": {"c": "DOUBLE", "s": "VARCHAR"}}, dialect="duckdb", normalize=False)
    return len(duckdb_sql(spark_coercions(parse(query)[0], None, schema)))


def _nested(value: str, depth: int) -> str:
    for _ in range(depth):
        value = f"array({value})"
    return value


@pytest.mark.parametrize("mix", [
    lambda d: f"array({_nested('c', d)}, {_nested(repr('a'), d)})",
    lambda d: f"coalesce({_nested('c', d)}, {_nested('s', d)})",
    lambda d: f"map(1, {_nested('c', d)}, 2, {_nested('s', d)})",
    lambda d: "coalesce(" * d + "c" + ", 'a')" * d,
])
def test_nested_mixes_grow_the_sql_linearly(mix):
    """Each value is cast once, in place, with one lambda per level of its arrays, so a level adds about the same
    SQL whatever is nested inside it."""
    sizes = [_size(f"SELECT {mix(depth)} FROM t") for depth in (2, 4, 8)]
    step = sizes[1] - sizes[0]
    assert sizes[2] - sizes[1] <= 2 * step + 1000, sizes
    assert sizes[2] < 200_000, sizes


# ---- max_by and min_by

@pytest.mark.parametrize(("query", "expected"), [
    ("SELECT max_by(v, k), min_by(v, k), typeof(max_by(v, k)) FROM VALUES (1, 10), (NULL, 20), (3, 5), (NULL, 1) AS t(v, k)",
     [(None, None, "int")]),
    ("SELECT max_by(v, k), min_by(v, k) FROM VALUES (1, 'b'), (NULL, 'c'), (3, 'a') AS t(v, k)", [(None, 3)]),
    (("SELECT g, max_by(v, k), min_by(v, k) FROM VALUES (1, 1, 10), (1, NULL, 20), (2, 3, 5), (2, NULL, 1), (3, NULL, NULL) "
     "AS t(g, v, k) GROUP BY g ORDER BY g"), [(1, None, 1), (2, 3, None), (3, None, None)]),
    ("SELECT max_by(v, k), min_by(v, k) FROM VALUES (1, NULL), (2, NULL) AS t(v, k)", [(None, None)]),
    ("SELECT max_by(v, k) FILTER (WHERE v > 0), max_by(DISTINCT v, k) FROM VALUES (1, 10), (NULL, 20), (3, 5) AS t(v, k)",
     [(1, None)]),
    (("SELECT v, k, max_by(v, k) OVER (ORDER BY k), min_by(v, k) OVER (ORDER BY k) FROM VALUES (1, 10), (NULL, 20), (3, 5), "
     "(NULL, 1) AS t(v, k) ORDER BY k"), [(None, 1, None, None), (3, 5, 3, None), (1, 10, 1, None), (None, 20, None, None)]),
    ("SELECT max_by(v, k) OVER (), min_by(v, k) OVER () FROM VALUES (1, 10), (NULL, 20), (3, 5), (NULL, 1) AS t(v, k) LIMIT 1",
     [(None, None)]),
    (("SELECT v, max_by(v, k) OVER (ORDER BY k ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) FROM VALUES (1, 10), (NULL, 20), "
     "(3, 5), (NULL, 1) AS t(v, k) ORDER BY k"), [(None, 3), (3, 1), (1, None), (None, None)]),
])
def test_max_by_and_min_by_keep_a_null_value(spark, query, expected):
    assert [tuple(r) for r in spark.sql(query).collect()] == expected


# ---- RANGE frames with an INTERVAL

_DATES = "FROM VALUES (date'2024-01-01', 1), (date'2024-01-02', 2), (date'2024-01-04', 4) AS t(d, x) ORDER BY d"


@pytest.mark.parametrize("frame", [
    "ORDER BY d RANGE BETWEEN INTERVAL 1 DAY PRECEDING AND CURRENT ROW",
    "ORDER BY d DESC RANGE BETWEEN INTERVAL '1' DAY PRECEDING AND INTERVAL '1' DAY FOLLOWING",
    "ORDER BY d RANGE BETWEEN CURRENT ROW AND INTERVAL 1 WEEK FOLLOWING",
    "PARTITION BY x ORDER BY d RANGE BETWEEN INTERVAL 2 HOUR PRECEDING AND CURRENT ROW",
])
def test_a_date_with_a_day_interval_range_is_refused(spark, frame):
    with pytest.raises(Exception, match=r"A RANGE frame given as an INTERVAL in days, hours, minutes or seconds can't "
                                        r"order by a date, so nothing ran\. Spark stops on it too \(\[DATATYPE_MISMATCH"
                                        r"\.RANGE_FRAME_INVALID_TYPE\]\)\. Order by a timestamp"):
        spark.sql(f"SELECT d, sum(x) OVER ({frame}) {_DATES}").collect()


@pytest.mark.parametrize(("query", "expected"), [
    (f"SELECT d, sum(x) OVER (ORDER BY d RANGE BETWEEN 1 PRECEDING AND CURRENT ROW) {_DATES}", [1, 3, 4]),
    (("SELECT d, sum(x) OVER (ORDER BY d RANGE BETWEEN INTERVAL 1 MONTH PRECEDING AND CURRENT ROW) FROM VALUES "
     "(date'2024-01-31', 1), (date'2024-02-29', 2), (date'2024-03-30', 4), (date'2024-03-31', 8), (date'2024-04-30', 16) "
     "AS t(d, x) ORDER BY d"), [1, 3, 6, 14, 28]),
    (("SELECT ts, sum(x) OVER (ORDER BY ts RANGE BETWEEN INTERVAL 1 HOUR PRECEDING AND CURRENT ROW) FROM VALUES "
     "(timestamp'2024-01-01 10:00:00', 1), (timestamp'2024-01-01 10:30:00', 2), (timestamp'2024-01-01 12:00:00', 4) "
     "AS t(ts, x) ORDER BY ts"), [1, 3, 4]),
    (("SELECT ts, sum(x) OVER (ORDER BY CAST(ts AS TIMESTAMP_NTZ) RANGE BETWEEN INTERVAL 1 DAY PRECEDING AND CURRENT ROW) "
     "FROM VALUES (timestamp'2024-01-01 10:00:00', 1), (timestamp'2024-01-02 09:30:00', 2), "
     "(timestamp'2024-01-03 12:00:00', 4) AS t(ts, x) ORDER BY ts"), [1, 3, 4]),
    (("SELECT k, sum(x) OVER (ORDER BY k RANGE BETWEEN 0.5 PRECEDING AND 0.5 FOLLOWING) FROM VALUES (1.5, 1), (2.0, 2), "
     "(2.6, 3), (4.0, 4) AS t(k, x) ORDER BY k"), [3, 3, 3, 4]),
    (("SELECT k, sum(x) OVER (ORDER BY k DESC RANGE BETWEEN 1 PRECEDING AND CURRENT ROW) FROM VALUES (1.5D, 1), (2.5D, 2), "
     "(2.6D, 3), (4.0D, 4) AS t(k, x) ORDER BY k"), [3, 5, 3, 4]),
    (("SELECT k, sum(x) OVER (ORDER BY k RANGE BETWEEN 1 PRECEDING AND CURRENT ROW) FROM VALUES (1.5D, 1), (double('NaN'), 2), "
     "(CAST(NULL AS DOUBLE), 3), (2.0D, 4) AS t(k, x) ORDER BY k"), [3, 1, 5, 2]),
])
def test_range_frames_spark_runs(spark, query, expected):
    assert [r[1] for r in spark.sql(query).collect()] == expected


@pytest.mark.parametrize("bounds", [
    "INTERVAL 1 DAY PRECEDING AND INTERVAL 2 HOUR FOLLOWING",
    "INTERVAL 1 MONTH PRECEDING AND INTERVAL 1 DAY FOLLOWING",
    "INTERVAL 1 YEAR PRECEDING AND INTERVAL 1 MONTH FOLLOWING",
])
def test_range_bounds_of_two_interval_units_are_refused(spark, bounds):
    with pytest.raises(ValueError, match=r"two types, so nothing ran\. Spark stops on it too \(\[DATATYPE_MISMATCH\."
                                         r"SPECIFIED_WINDOW_FRAME_DIFF_TYPES\]\)\. Give both bounds in one unit"):
        spark.sql(f"SELECT sum(x) OVER (ORDER BY ts RANGE BETWEEN {bounds}) FROM VALUES "
                  "(timestamp'2024-01-01 10:00:00', 1) AS t(ts, x)").collect()


# ---- concat_ws of an array column

@pytest.mark.parametrize(("expression", "expected"), [
    ("concat_ws(',', sq)", "p,q"), ("concat_ws(',', ai)", "1,2"), ("concat_ws(',', ax, s, ai)", "x,a,1,2"),
    ("concat_ws(',', concat_ws('-', sq), sq)", "p-q,p,q"), ("concat_ws(s, ai)", "1a2"),
    ("concat_ws(',', coalesce(sq, sq))", "p,q"), ("concat_ws(',', CAST(ai AS ARRAY<STRING>))", "1,2"),
    ("concat_ws(',', d, i, CAST(NULL AS ARRAY<STRING>))", "1.5,1"),
])
def test_concat_ws_joins_an_array_column(spark, expression, expected):
    assert row(spark, f"SELECT {expression} FROM kf_mix") == (expected,)


def test_concat_ws_with_a_separator_from_the_data():
    query = ("SELECT concat_ws(s, a, 'z') AS v FROM VALUES ('-', array('p', 'q')), (NULL, array('p')), "
             "('+', CAST(NULL AS ARRAY<STRING>)), ('+', array()) AS t(s, a)")
    assert [r["v"] for r in sql.run(query).to_pylist()] == ["p-q-z", None, "z", "z"]


def test_concat_ws_of_an_array_of_arrays_is_refused(spark):
    with pytest.raises(Exception, match=r"concat_ws\(\) takes only text or arrays of text, and this is an array of "
                                        r"arrays, so nothing ran on this PC\. Spark stops on it too"):
        spark.sql("SELECT concat_ws(',', aa) FROM kf_mix").collect()


def test_concat_ws_with_a_separator_from_the_data_grows_linearly():
    """The separator and the parts are each read once, so nesting adds about the same SQL per level."""
    from fabric_desktop.sql import duckdb_sql, parse
    sizes = []
    for depth in (2, 4, 8):
        e = "c"
        for _ in range(depth):
            e = f"concat_ws(s, {e}, b)"
        sizes.append(len(duckdb_sql(parse(f"SELECT {e} FROM t")[0])))
    step = sizes[1] - sizes[0]
    assert sizes[2] - sizes[1] <= 2 * step + 1000, sizes
    assert sizes[2] < 200_000, sizes


def test_the_duckdb_engines_spark_sql_takes_the_same_types():
    """A local data project's Spark SQL cell on the DuckDB engine (engines/local_sql.py) types the values too."""
    from fabric_desktop.engines import local_sql
    from fabric_desktop.engines.contract import SessionConfig
    session = local_sql.DuckDBSession(SessionConfig("nb"))
    try:
        assert "CAST(1 AS TEXT)" in session._sql("SELECT array(1, 'a')", "spark")
        with pytest.raises(local_sql.Unsupported, match=r"^array\(\) can't take boolean and int values together"):
            session._sql("SELECT array(true, 1)", "spark")
    finally:
        session.close()

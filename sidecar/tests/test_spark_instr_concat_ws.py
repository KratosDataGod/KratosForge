"""instr, locate and position with a number to find, and concat_ws of a map or struct, as Spark 3.5.3 gives them.

Spark casts the text to find to a string, so instr(12345, 34) is 3, where DuckDB stopped with a binder error. Spark
refuses concat_ws of a map or struct ([DATATYPE_MISMATCH.UNEXPECTED_INPUT_TYPE]), where DuckDB wrote its own text of
it ({'a': 1}); here it is refused by name. Expected values were read from Apache Spark 3.5.3 (local[1]).
"""

from __future__ import annotations


import pytest

from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark():
    session = Kernel().namespace["spark"]
    session.createDataFrame([(1.5, "a", 7)], "d double, t string, i int").createOrReplaceTempView("kf_iw")
    return session


def one(spark, expression: str):
    return spark.sql(f"SELECT {expression} AS v").collect()[0][0]


@pytest.mark.parametrize(("expression", "expected"), [
    ("instr(12345, 34)", 3),
    ("instr(12345, '34')", 3),
    ("instr('12345', 34)", 3),
    ("instr(1.5D, '.')", 2),
    ("instr(NULL, 'a')", None),
    ("locate(34, 12345)", 3),
    ("locate('3', 12345, 2)", 3),
    ("position(34 IN 12345)", 3),
    ("concat_ws(',', -0.0)", "0.0"),
    ("concat_ws(',', -0.0D)", "-0.0"),
    ("concat_ws(',', -0.00, 1.50)", "0.00,1.50"),
    ("concat_ws(',', CAST(-0.0 AS DECIMAL(3,1)))", "0.0"),
    ("concat_ws(',', array(1,2))", "1,2"),
    ("concat_ws(',', 1, true, DATE'2024-01-01')", "1,true,2024-01-01"),
])
def test_gives_sparks_answer(spark, expression, expected):
    assert one(spark, expression) == expected


def test_columns_keep_sparks_names_and_answers(spark):
    frame = spark.sql("SELECT concat_ws('-', d, t, i, array(t, t)), instr(i * 10, i), locate(i, 1234567) FROM kf_iw")
    assert [tuple(r) for r in frame.collect()] == [("1.5-a-7-a-a", 1, 7)]
    assert frame.columns[1:] == ["instr((i * 10), i)", "locate(i, 1234567, 1)"]


@pytest.mark.parametrize("expression", [
    "concat_ws(',', named_struct('a', 1))", "concat_ws(',', map(1, 2))", "concat_ws('-', named_struct('x', i))",
    "concat_ws('-', struct(i, t))", "concat_ws('-', array(named_struct('x', i)))", "concat_ws('-', array(map(1, 2)))",
])
def test_a_map_or_struct_is_refused_by_name(spark, expression):
    with pytest.raises(Exception, match=r"concat_ws\(\) takes only text or arrays of text, and this is a map or struct, so nothing ran"):
        spark.sql(f"SELECT {expression} FROM kf_iw").collect()


def test_a_struct_column_is_refused_when_it_runs(spark):
    spark.sql("CREATE OR REPLACE TEMP VIEW kf_iws AS SELECT named_struct('x', i) AS st, t FROM kf_iw")
    with pytest.raises(Exception, match=r"concat_ws\(\) takes only text or arrays of text, and this is a map or struct, so nothing ran"):
        spark.sql("SELECT concat_ws('-', st) FROM kf_iws").collect()
    assert spark.sql("SELECT concat_ws('-', t, t) FROM kf_iws").collect()[0][0] == "a-a"


@pytest.mark.parametrize(("expression", "expected"), [
    ("locate('b', concat('ab', locate('b', 'abcb', 3)), 1)", 2),
    ("locate(substr('xbz', 2, 1), 'abcb', locate('c', 'abcb', 1))", 4),
    ("locate('1', 'x1y1', locate('y', concat('aay', NULL), 1))", 0),
    ("locate('b', upper('abab'), 0)", 0),
    ("instr(locate('b', 'abb', 3), 3)", 1),
])
def test_nested_locate_gives_sparks_answer(spark, expression, expected):
    assert one(spark, expression) == expected


def _size(query: str) -> int:
    from fabric_desktop.sql import duckdb_sql, parse
    return len(duckdb_sql(parse(query)[0]))


@pytest.mark.parametrize("nest", [
    lambda e: f"locate(b, {e}, 2)", lambda e: f"locate({e}, b, 2)", lambda e: f"instr({e}, b)",
    lambda e: f"concat_ws('-', {e}, upper(c))",
])
def test_nesting_grows_the_sql_linearly(nest):
    """Each argument is read once, so a level adds about the same SQL whatever is nested inside it (locate(b, <expr>,
    2) once grew 3x per level: 3 MB and half a minute to translate at depth 6)."""
    sizes = []
    for depth in (2, 4, 8):
        e = "c"
        for _ in range(depth):
            e = nest(e)
        sizes.append(_size(f"SELECT {e} FROM t"))
    step = sizes[1] - sizes[0]
    assert sizes[2] - sizes[1] <= 2 * step + 1000, sizes
    assert sizes[2] < 200_000, sizes

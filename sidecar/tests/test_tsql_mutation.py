"""Mutation batch 150: what a warehouse user sees from the local T-SQL translator (localdata/tsql.py).

Each test runs T-SQL through the local engine and checks the rows it gives, or the exact words it stops with.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("Translator")["id"])
    e.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, s)")
    yield e
    data.close_all()


def run(engine, sql):
    return engine.execute(sql, dialect="tsql")["rows"]


def refused(engine, sql):
    with pytest.raises(ValueError) as caught:
        engine.execute(sql, dialect="tsql")
    return str(caught.value)


def test_a_decimal_product_whose_scales_add_up_past_38_is_worked_out_in_floating_point(engine):
    # DuckDB can't type DECIMAL(38, 20) * DECIMAL(38, 19) (a scale of 39); T-SQL gives DECIMAL(38, 6)
    assert run(engine, "SELECT CAST(1.5 AS DECIMAL(38, 20)) * CAST(2 AS DECIMAL(38, 19))") == [["3.000000"]]


@pytest.mark.parametrize("sql", ["SELECT CAST(RAND() AS DECIMAL(10, 4)) / CAST(1 AS DECIMAL(10, 4))",
                                 "SELECT CAST(RAND() AS DECIMAL(38, 4)) * CAST(1 AS DECIMAL(38, 4))"])
def test_rand_in_decimal_arithmetic_is_drawn_once(engine, sql):
    # the exact decimal forms read each side more than once, which would draw RAND() again and give another value
    engine.execute("SELECT setseed(0.3)")
    value = run(engine, sql)[0][0]
    engine.execute("SELECT setseed(0.3)")
    first = engine.execute("SELECT CAST(random() AS DECIMAL(10, 4))")["rows"][0][0]
    assert Decimal(value) == Decimal(first)


def test_a_text_too_long_for_its_column_names_the_table_and_column(engine):
    engine.execute("CREATE TABLE dbo.v (s VARCHAR(3))", dialect="tsql")
    with pytest.raises(Exception, match=r"String or binary data would be truncated in table 'dbo\.v', column 's'\."):
        engine.execute("INSERT INTO dbo.v VALUES ('abcd')", dialect="tsql")


def test_a_temp_table_with_a_distribution_runs(engine):
    assert run(engine, "CREATE TABLE #w (n INT) WITH (DISTRIBUTION = ROUND_ROBIN); INSERT INTO #w VALUES (1); "
                       "SELECT n FROM #w") == [[1]]


@pytest.mark.parametrize("sql, value", [
    # T-SQL's scale for a division is max(6, s1 + p2 + 1), and a SMALLINT divisor counts as 5 digits, a TINYINT as 3
    ("SELECT CAST(1 AS DECIMAL(5, 2)) / CAST(3 AS SMALLINT)", "0.33333333"),
    ("SELECT CAST(1 AS DECIMAL(5, 2)) / CAST(3 AS TINYINT)", "0.333333"),
    ("SELECT CAST(1 AS DECIMAL(5, 2)) / CAST(3 AS INT)", "0.3333333333333"),
])
def test_a_decimal_divided_by_an_integer_has_tsqls_scale(engine, sql, value):
    assert run(engine, sql) == [[value]]


def test_an_alter_view_that_isnt_as_select_is_refused_with_the_shape(engine):
    engine.execute("CREATE VIEW v AS SELECT id FROM t", dialect="tsql")
    assert refused(engine, "ALTER VIEW v ADD x INT") == (
        "This T-SQL can't be run on the local engine: this ALTER VIEW isn't mapped yet. Use ALTER VIEW name AS "
        "SELECT ...")
    assert run(engine, "SELECT COUNT(*) FROM v") == [[3]]


@pytest.mark.parametrize("column", ["id BIGINT IDENTITY", "id BIGINT NOT NULL IDENTITY"])
def test_an_identity_column_is_refused_by_name_and_makes_no_table(engine, column):
    assert refused(engine, f"CREATE TABLE dbo.x ({column}, n INT)").startswith(
        "This T-SQL can't be run on the local engine: IDENTITY isn't mapped yet (column id).")
    assert engine.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'x'")["rows"] == [[0]]


def test_a_view_keeps_the_schema_it_names_and_reads_its_own_cte(engine):
    engine.execute("CREATE SCHEMA sales")
    engine.execute("CREATE TABLE sales.o AS SELECT * FROM (VALUES (1), (2)) v(id)")
    engine.execute("CREATE VIEW dbo.vo AS SELECT id FROM sales.o", dialect="tsql")
    engine.execute("CREATE VIEW dbo.vc AS WITH c AS (SELECT id FROM t) SELECT COUNT(*) AS n FROM c", dialect="tsql")
    assert run(engine, "SELECT COUNT(*) FROM dbo.vo") == [[2]]
    assert run(engine, "SELECT n FROM dbo.vc") == [[3]]


def test_str_takes_a_length_from_1_to_8000_and_decimals_from_0_to_16(engine):
    assert run(engine, "SELECT LEN(STR(1, 8000))") == [[8000]]
    assert run(engine, "SELECT STR(5, 1), STR(2.4, 3, 0), STR(2.25, 6, 16)") == [["5", "  2", "2.2500"]]
    assert refused(engine, "SELECT STR(1, 8001)") == (
        "This T-SQL can't be run on the local engine: STR's length has to be a whole number from 1 to 8000 written in "
        "the query.")


@pytest.mark.parametrize("sql", ["SELECT ISJSON()", "SELECT ISJSON('[]', ARRAY, OBJECT)"])
def test_isjson_with_no_text_or_too_many_arguments_is_refused(engine, sql):
    assert refused(engine, sql) == "ISJSON takes the text, and optionally VALUE, ARRAY, OBJECT or SCALAR."


def test_string_split_with_a_null_separator_is_refused_by_name(engine):
    assert refused(engine, "SELECT value FROM STRING_SPLIT('a,b', NULL)") == (
        "This T-SQL can't be run on the local engine: STRING_SPLIT with a NULL separator isn't mapped yet. "
        "Microsoft's page doesn't say what a warehouse returns. Give it a separator of one character.")


def test_the_literal_zero_point_counts_as_one_digit_in_a_wide_product(engine):
    # 0. is DECIMAL(1, 0): times DECIMAL(38, 10) that is precision 40 cut to 38, keeping 8 places (not 9)
    value = run(engine, "SELECT 0. * CAST(2 AS DECIMAL(38, 10))")[0][0]
    assert Decimal(value) == 0 and Decimal(value).as_tuple().exponent == -8


def test_an_unchecked_convert_style_says_which_styles_are_checked(engine):
    result = engine.execute("SELECT CONVERT(VARCHAR(10), CAST('2026-11-18' AS DATE), 104)", dialect="tsql")
    assert result["rows"] == [["18.11.2026"]]
    assert result["translation"]["notes"] == [(
        "CONVERT style 104 runs as a translation that hasn't been checked against T-SQL's answers; styles 101, 103, "
        "112, 120, 121 and 126 have been. Compare its values in Fabric if they matter.")]


def test_dbcc_after_another_statement_is_refused_by_name(engine):
    assert refused(engine, "SELECT 1; DBCC CHECKIDENT ('t')") == (
        "This T-SQL can't be run on the local engine: DBCC isn't mapped yet. Run it in a Fabric Warehouse.")

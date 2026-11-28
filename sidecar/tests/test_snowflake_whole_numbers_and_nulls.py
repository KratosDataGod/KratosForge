"""Snowflake whole-number casts, MONTHS_BETWEEN, ROUND's rounding modes, NULLs in ARRAY_CAT, ARRAY_AGG and
LISTAGG, and math edges Snowflake's pages leave open (WR-279: SFX-080 to SFX-083, SFN-080 to SFN-084).

Each expected answer is taken from the Snowflake SQL reference page cited beside it. Each case runs through
snowflake_sql.translate and DuckDB, as a Snowflake cell does here, on both DuckDB versions this PC runs.
"""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf

NUMERIC = "https://docs.snowflake.com/en/sql-reference/data-types-numeric"

# (expression, Snowflake's answer). A float answer is compared to 1e-6, as the pages print six places.
CASES: list[tuple[str, object]] = [
    # data-types-numeric: INT, INTEGER, BIGINT, SMALLINT, TINYINT, BYTEINT are "Synonymous with NUMBER, except
    # that precision and scale can't be specified (that is, it always defaults to NUMBER(38, 0))". So a cast to
    # one is a cast to NUMBER: 2.5::FLOAT::NUMBER is 3 here, and so 2.5::FLOAT::INT is too (DuckDB gave 2), and
    # 3000000000 fits (DuckDB's 32-bit INT stopped, and TRY_CAST gave NULL).
    ("2.5::FLOAT::INT", 3),
    ("CAST(-2.5::FLOAT AS INTEGER)", -3),
    ("0.5::DOUBLE::BIGINT", 1),
    ("PARSE_JSON('{\"a\": 2.5}'):a::INT", 3),
    ("3000000000::INT", 3000000000),
    ("3000000000::INTEGER * 4", 12000000000),
    ("TRY_CAST(3000000000 AS INT)", 3000000000),
    ("40000::SMALLINT", 40000),
    ("300::TINYINT", 300),
    ("'2.5'::INT", 3),
    ("TRY_CAST('x' AS INT)", None),
    ("NULL::INT", None),
    ("REPEAT('a', '2'::INT)", "aa"),
    # months_between (https://docs.snowflake.com/en/sql-reference/functions/months_between): "the fractional
    # portion is calculated using the day and time parts of the input values", a month counted as 31 days
    ("MONTHS_BETWEEN('2019-03-01 02:00:00'::TIMESTAMP, '2019-02-15 01:00:00'::TIMESTAMP)", 0.549731),
    ("MONTHS_BETWEEN('2019-02-15 02:00:00'::TIMESTAMP, '2019-02-15 01:00:00'::TIMESTAMP)", 0.0),
    ("MONTHS_BETWEEN('2019-03-01'::DATE, '2019-02-15'::DATE)", 0.548387),
    ("MONTHS_BETWEEN('2019-03-28'::DATE, '2019-02-28'::DATE)", 1.0),
    ("MONTHS_BETWEEN('2019-03-30'::DATE, '2019-02-28'::DATE)", 1.064516),
    ("MONTHS_BETWEEN('2019-03-31'::DATE, '2019-02-28'::DATE)", 1.0),
    ("MONTHS_BETWEEN('2019-02-01'::DATE, '2019-03-01'::DATE)", -1.0),
    ("ROUND(MONTHS_BETWEEN('2019-03-31 12:00:00'::TIMESTAMP, '2019-02-28 00:00:00'::TIMESTAMP))", 1.0),
    ("MONTHS_BETWEEN(NULL, '2019-03-01'::DATE)", None),
    # round (https://docs.snowflake.com/en/sql-reference/functions/round): 'HALF_TO_EVEN' on a NUMBER; "Data
    # types for floating point numbers (for example, FLOAT) aren't supported with this argument."
    ("ROUND(2.5, 0, 'HALF_TO_EVEN')", Decimal(2)),
    ("ROUND(-2.5, 0, 'HALF_TO_EVEN')", Decimal(-2)),
    ("ROUND(1.005, 2, 'HALF_TO_EVEN')", Decimal("1.00")),
    ("ROUND(1.015, 2, 'HALF_TO_EVEN')", Decimal("1.02")),
    ("ROUND(25, -1, 'HALF_TO_EVEN')", 20),
    ("ROUND(35, -1, 'HALF_TO_EVEN')", 40),
    ("ROUND(2.5, 0)", Decimal(3)),
    ("ROUND(-2.5, 0, 'HALF_AWAY_FROM_ZERO')", Decimal(-3)),
    # array_cat (https://docs.snowflake.com/en/sql-reference/functions/array_cat): "If either argument is NULL,
    # the function returns NULL without reporting any error."
    ("ARRAY_CAT(ARRAY_CONSTRUCT(1), NULL)", None),
    ("ARRAY_CAT(ARRAY_CONSTRUCT(1), b) FROM (SELECT NULL::ARRAY AS b)", None),
    ("ARRAY_CAT(ARRAY_CONSTRUCT(1), ARRAY_CONSTRUCT(2))", [1, 2]),
    # array_agg (https://docs.snowflake.com/en/sql-reference/functions/array_agg): "If the input is empty, the
    # function returns an empty array"; "NULL values are omitted from the output."
    ("ARRAY_AGG(x) FROM (SELECT 1 AS x WHERE FALSE)", []),
    ("ARRAY_AGG(x) FROM (SELECT NULL::INT AS x)", []),
    ("ARRAY_AGG(x) WITHIN GROUP (ORDER BY x DESC) FROM (SELECT 1 AS x UNION ALL SELECT 2)", [2, 1]),
    # listagg (https://docs.snowflake.com/en/sql-reference/functions/listagg): "If no delimiter is specified,
    # an empty string is used as the delimiter." "If the input is empty, an empty string is returned." "If all
    # input expressions evaluate to NULL, the output is an empty string."
    ("LISTAGG(x) WITHIN GROUP (ORDER BY x) FROM (SELECT 'a' AS x UNION ALL SELECT 'b')", "ab"),
    ("LISTAGG(x, ',') FROM (SELECT 'a' AS x WHERE FALSE)", ""),
    ("LISTAGG(x, ',') FROM (SELECT NULL::VARCHAR AS x)", ""),
    ("LISTAGG(x, '-') OVER (PARTITION BY 1) FROM (SELECT NULL::VARCHAR AS x)", ""),
    # what still runs as before
    ("REGR_SLOPE(y, x) FROM (VALUES (1, 1), (2, 2)) AS t(y, x)", 1.0),
    ("IFF(REGR_SXX(y, x) = 0, NULL, REGR_SLOPE(y, x)) FROM (VALUES (1, 1)) AS t(y, x)", None),
    ("LN(1)", 0.0),
    ("SQRT(4)", 2.0),
    ("LOG(10, 100)", 2.0),
    ("MOD(-3, 2)", -1),  # mod: the remainder "from truncation-based division"
    ("MOD(7, NULL)", None),
]

# Stopped by name where the query runs: (expression, rule id).
STOPS = [
    ("TRY_CAST('99999999999999999999' AS INT)", "SFN-080"),
    ("CAST(1e19 AS BIGINT)", "SFN-080"),
    ("ROUND(2.5::FLOAT, 0, 'HALF_TO_EVEN')", "SFN-081"),
    ("ROUND(2.5::FLOAT, 0, 'HALF_AWAY_FROM_ZERO')", "SFN-081"),
    ("REGR_SLOPE(y, x) FROM (VALUES (1, 1)) AS t(y, x)", "SFN-082"),
    ("REGR_SLOPE(y, x) OVER () FROM (VALUES (1, 5), (2, 5)) AS t(y, x)", "SFN-082"),
    ("LN(0)", "SFN-083"),
    ("LN(-1)", "SFN-083"),
    ("SQRT(-1)", "SFN-083"),
    ("LOG(10, 0)", "SFN-083"),
    ("LOG(1, 5)", "SFN-083"),
    ("MOD(7, 0)", "SFN-084"),
    ("7 % 0", "SFN-084"),
]


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def _run(con: duckdb.DuckDBPyConnection, expression: str) -> object:
    translation = sf.translate("SELECT " + expression)
    assert translation.runnable, translation.first_refusal()
    return con.execute(translation.duckdb_sql).fetchall()[0][0]


def _same(want: object, got: object) -> bool:
    if isinstance(want, float):
        return isinstance(got, float) and abs(got - want) < 1e-6
    if isinstance(want, Decimal):
        return isinstance(got, Decimal) and got == want
    if isinstance(want, int) and not isinstance(want, bool):
        return isinstance(got, int) and not isinstance(got, bool) and got == want
    return type(want) is type(got) and want == got


@pytest.mark.parametrize(("expression", "want"), CASES)
def test_answers_as_snowflakes_reference_says(con: duckdb.DuckDBPyConnection, expression: str, want: object) -> None:
    got = _run(con, expression)
    assert _same(want, got), f"SELECT {expression}: Snowflake {want!r}, here {got!r}"


@pytest.mark.parametrize(("expression", "rule"), STOPS)
def test_stops_by_name_where_snowflake_differs_or_its_page_is_silent(
        con: duckdb.DuckDBPyConnection, expression: str, rule: str) -> None:
    translation = sf.translate("SELECT " + expression)
    assert translation.runnable
    with pytest.raises(duckdb.Error) as caught:
        con.execute(translation.duckdb_sql).fetchall()
    assert rule in str(caught.value) and sf.stopped_by_name(caught.value)


@pytest.mark.parametrize("expression", ["ROUND(2.5, 0, 'half_to_even')", "ROUND(x, 0, m) FROM t",
                                        "ROUND(x, s, 'HALF_TO_EVEN') FROM t", "ROUND(2.5, 0, 'BANKERS')"])
def test_a_rounding_mode_not_rewritten_is_refused_by_name(expression: str) -> None:
    translation = sf.translate("SELECT " + expression)
    assert not translation.runnable and "ROUND with this rounding mode" in translation.first_refusal()


def test_an_int_column_holds_snowflakes_whole_numbers(con: duckdb.DuckDBPyConnection) -> None:
    # data-types-numeric: an INT column is NUMBER(38, 0)
    translation = sf.translate("CREATE OR REPLACE TEMPORARY TABLE wr279 (a INT, b SMALLINT, c BYTEINT);\n"
                               "INSERT INTO wr279 VALUES (3000000000, 70000, 300);\nSELECT a, b, c FROM wr279")
    assert translation.runnable
    assert con.execute(translation.duckdb_sql).fetchall() == [(3000000000, 70000, 300)]


def test_rewrites_read_each_argument_once() -> None:
    # nested 12 deep, the SQL grows by about the same amount at each level
    for template in ("{}::INT + 1", "MONTHS_BETWEEN({}, '2020-01-01'::DATE)::DATE", "ARRAY_CAT({}, ARRAY_CONSTRUCT(1))",
                     "ROUND({}, 1, 'HALF_TO_EVEN')", "LN({})", "MOD({}, x)", "LOG(2, {})"):
        sizes = []
        expression = "x"
        for _ in range(12):
            expression = template.format(expression)
            translation = sf.translate(f"SELECT {expression} FROM t")
            sizes.append(len(translation.duckdb_sql) if translation.runnable else 0)
        steps = [b - a for a, b in zip(sizes, sizes[1:], strict=False)]
        assert max(steps) <= 2 * min(steps) + 50, (template, sizes)


def test_a_call_another_rule_writes_is_left_alone() -> None:
    # HAVERSINE's own SQRT never takes a negative number, so it gets no SFN-083 check
    assert "SFN-083" not in sf.translate("SELECT HAVERSINE(1, 2, 3, 4)").duckdb_sql
    # a whole number written in the query is cast as it is
    assert "CAST(5 AS BIGINT)" in sf.translate("SELECT 5::INT").duckdb_sql


def test_the_rules_are_in_the_catalog() -> None:
    ids = {row[0] for row in sf.CATALOG}
    assert {"SFX-080", "SFX-081", "SFX-082", "SFX-083", "SFN-080", "SFN-081", "SFN-082", "SFN-083", "SFN-084"} <= ids
    assert sf.RULES_VERSION >= "2026.11.28" and NUMERIC.startswith("https://docs.snowflake.com/")

"""WR-206: decimal arithmetic in local T-SQL gets a Fabric Warehouse's precision and scale.

Each +, -, *, / with a decimal side, and SUM and AVG of a decimal, is compared with T-SQL's rules
("Precision, scale, and length" on Microsoft Learn). A case that can't be worked out exactly here says so
in the run's notes; none differs silently.
"""

from __future__ import annotations

import itertools
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext

import duckdb
import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import tsql_decimal

# column -> (T-SQL type, value, (precision, scale) as T-SQL sees it in arithmetic)
COLUMNS = {
    "a": ("DECIMAL(5,2)", "-10.25", (5, 2)),
    "b": ("DECIMAL(7,3)", "3.125", (7, 3)),
    "c": ("DECIMAL(38,0)", "7", (38, 0)),
    "d": ("DECIMAL(38,10)", "1.5", (38, 10)),
    "w": ("DECIMAL(38,10)", "1234567890123456789012345678.5", (38, 10)),
    "i": ("INT", "3", (10, 0)),
    "g": ("BIGINT", "-4", (19, 0)),
}
FLOAT_NOTE = "done in floating point"


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("dec") / "app" / "local-data")
    e = local.engine(local.create("Decimals")["id"])
    picks = ", ".join(f"CAST('{v}' AS {t.replace('INT', 'INTEGER') if t == 'INT' else t}) AS {n}"
                      for n, (t, v, _) in COLUMNS.items())
    e.execute(f"CREATE TABLE dbo.n AS SELECT {picks}")
    return e


def tsql(engine, sql):
    return engine.execute(sql, dialect="tsql")


def _type(result):
    kind = result["columns"][0]["type"].lower().replace(" ", "")
    assert kind.startswith("decimal"), kind
    p, s = kind[kind.index("(") + 1:kind.index(")")].split(",")
    return int(p), int(s)


def _notes(result):
    return " ".join((result.get("translation") or {}).get("notes") or [])


@pytest.mark.parametrize(("p1", "s1", "op", "p2", "s2", "want"), [
    (5, 2, "+", 7, 3, (8, 3)),
    (38, 10, "+", 38, 0, (38, 0)),  # past 38 digits, scale gives way to the whole part
    (38, 10, "+", 30, 5, (38, 10)),  # past 38 digits, and the whole part leaves room for all the scale
    (5, 2, "-", 10, 0, (13, 2)),
    (5, 2, "*", 7, 3, (13, 5)),
    (38, 10, "*", 38, 10, (38, 6)),  # 57 whole digits: scale is cut to 6
    (38, 10, "*", 5, 2, (38, 6)),  # 32 whole digits: scale 12 is cut to 6
    (30, 10, "*", 2, 1, (33, 11)),  # fits in 38: nothing is cut
    (20, 2, "*", 10, 2, (31, 4)),
    (36, 4, "*", 5, 4, (38, 6)),  # exactly 32 whole digits
    (5, 2, "/", 7, 3, (16, 10)),
    (18, 0, "/", 18, 0, (37, 19)),
    (38, 10, "/", 38, 10, (38, 6)),
    (38, 10, "/", 5, 2, (38, 8)),  # 30 whole digits: scale is 38 - 30
])
def test_the_rules_give_the_documented_precision_and_scale(p1, s1, op, p2, s2, want):
    assert tsql_decimal(op, p1, s1, p2, s2) == want


PAIRS = [(x, y) for x, y in itertools.product(COLUMNS, repeat=2)
         if COLUMNS[x][0].startswith("DECIMAL") or COLUMNS[y][0].startswith("DECIMAL")]


def _expected(op, x, y, scale):
    with localcontext() as ctx:
        ctx.prec = 100
        a, b = Decimal(COLUMNS[x][1]), Decimal(COLUMNS[y][1])
        exact = {"+": a + b, "-": a - b, "*": a * b, "/": a / b}[op]
        # T-SQL cuts a decimal quotient at the result's scale, and rounds a sum, difference or product it has to
        # narrow (as a CAST does).
        return exact.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_DOWN if op == "/" else ROUND_HALF_UP)


@pytest.mark.parametrize("op", ["+", "-", "*", "/"])
def test_every_decimal_operation_has_the_warehouse_type_and_value(engine, op):
    for x, y in PAIRS:
        want = tsql_decimal(op, *COLUMNS[x][2], *COLUMNS[y][2])
        if (x, op, y) == ("w", "*", "w"):
            continue  # too big for its type: see the next test
        r = tsql(engine, f"SELECT {x} {op} {y} AS v FROM dbo.n")
        assert _type(r) == want, (x, op, y)
        got = Decimal(str(r["rows"][0][0]))
        expected = _expected(op, x, y, want[1])
        if "w" in (x, y) and FLOAT_NOTE in _notes(r):
            # 28 whole digits: too wide to work out exactly here, so floating point, and the run says so.
            assert abs(got - expected) <= abs(expected) * Decimal("1e-14") + Decimal(1).scaleb(-want[1]), (x, op, y)
        else:
            assert got == expected, (x, op, y, got, expected)  # exact, to the last digit


def test_a_result_too_big_for_its_type_stops_as_the_warehouse_does(engine):
    # A warehouse says "Arithmetic overflow error converting expression to data type numeric."
    with pytest.raises(duckdb.ConversionException, match=r"to DECIMAL\(38,6\)"):
        tsql(engine, "SELECT w * w AS v FROM dbo.n")


def test_a_decimal_quotient_is_exact_and_cut_toward_zero(engine):
    r = tsql(engine, "SELECT a / i AS v FROM dbo.n")
    assert _type(r) == (16, 13) and r["rows"] == [["-3.4166666666666"]]  # not ...67, and no float noise
    assert FLOAT_NOTE not in _notes(r)
    r = tsql(engine, "SELECT w / a AS v FROM dbo.n")  # 28 whole digits still divide exactly
    assert _type(r) == (38, 8) and r["rows"] == [["-120445647816922613562180066.19512195"]]


def test_dividing_a_decimal_by_zero_stops_as_the_warehouse_does(engine):
    with pytest.raises(Exception, match="Divide by zero error encountered"):
        tsql(engine, "SELECT a / (i - 3) AS v FROM dbo.n")


def test_a_division_too_wide_to_do_exactly_says_so(engine):
    r = tsql(engine, "SELECT c / a AS v FROM dbo.n")
    assert _type(r) == (38, 6)
    assert FLOAT_NOTE in _notes(r)


def test_integers_still_divide_as_integers(engine):
    r = tsql(engine, "SELECT g / i AS v, i * g AS m FROM dbo.n")
    assert r["rows"] == [[-1, -12]]
    assert FLOAT_NOTE not in _notes(r)


@pytest.mark.parametrize(("column", "sum_type", "avg_type"), [
    ("a", (38, 2), (38, 6)),
    ("b", (38, 3), (38, 6)),
    ("d", (38, 10), (38, 10)),
    ("c", (38, 0), (38, 6)),
])
def test_sum_and_avg_of_a_decimal_have_the_warehouse_type(engine, column, sum_type, avg_type):
    s = tsql(engine, f"SELECT SUM({column}) AS v FROM dbo.n")
    assert _type(s) == sum_type and FLOAT_NOTE not in _notes(s)
    a = tsql(engine, f"SELECT AVG({column}) AS v FROM dbo.n")
    assert _type(a) == avg_type
    assert Decimal(str(a["rows"][0][0])) == Decimal(COLUMNS[column][1])
    assert FLOAT_NOTE in _notes(a)  # the average itself is worked out in floating point


def test_a_decimal_average_over_a_window_has_the_warehouse_type(engine):
    r = tsql(engine, "SELECT AVG(b) OVER () AS v FROM dbo.n")
    assert _type(r) == (38, 6) and r["rows"] == [["3.125000"]]


@pytest.mark.parametrize(("expression", "want", "value"), [
    ("(a / b) / i", (27, 21), "-1.093333333333333333333"),  # the inner quotient is cut at its own scale first
    ("a * 1.5", (8, 3), "-15.375"),  # 1.5 is decimal(2, 1)
    ("7 / 2.0", (8, 6), "3.500000"),  # a whole number written in the query is decimal(its digits, 0)
    ("1.0 / 3", (7, 6), "0.333333"),
    ("-a / 0.5", (10, 6), "20.500000"),
    ("a * 3000000000", (16, 2), "-30750000000.00"),  # too big for an int: decimal(10, 0)
    ("i * 2147483648", (21, 0), "6442450944"),  # 2^31, the first whole number too big for an int, is a decimal
    ("(a * b) + (a / i)", (22, 13), "-35.4479166666666"),
    ("SUM(a) * 2", (38, 2), "-20.50"),
    ("AVG(a) / 2", (38, 6), "-5.125000"),
])
def test_nested_math_literals_and_aggregates_get_the_warehouse_type(engine, expression, want, value):
    r = tsql(engine, f"SELECT {expression} AS v FROM dbo.n")
    assert _type(r) == want
    assert r["rows"] == [[value]]


def test_a_product_too_wide_to_work_out_exactly_says_so(engine):
    r = tsql(engine, "SELECT w * a AS v, a * d AS small FROM dbo.n")
    assert [c["type"].replace(" ", "") for c in r["columns"]] == ["decimal128(38,6)", "decimal128(38,6)"]
    assert r["rows"][0][1] == "-15.375000"  # a small value in the same kind of product is still exact
    assert FLOAT_NOTE in _notes(r)


def test_scales_that_add_up_past_38_still_multiply(engine):
    # DuckDB can't type decimal(38,20) * decimal(38,20); T-SQL gives decimal(38,6).
    r = tsql(engine, "SELECT CAST(1.5 AS DECIMAL(38,20)) * CAST(2 AS DECIMAL(38,20)) AS v FROM dbo.n")
    assert _type(r) == (38, 6) and r["rows"] == [["3.000000"]]
    assert FLOAT_NOTE in _notes(r)

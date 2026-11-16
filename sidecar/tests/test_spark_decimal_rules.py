"""Spark SQL on this PC gives Spark's decimal precision, scale and value for each operator and
function Spark types by its own rules (WR-105). The expected type comes from Spark's source, written
out here independently of the engine:

- Divide, Multiply, Add, Subtract, Remainder and Pmod: DecimalPrecision and arithmetic.scala, with
  spark.sql.decimalOperations.allowPrecisionLoss on (the default), so a precision over 38 keeps the
  whole digits and cuts the scale to no fewer than 6 (DecimalType.adjustPrecisionScale);
- Average: decimal(p + 4, s + 4), bounded to 38;
- IntegralDivide (div): LongType, truncated toward zero, NULL for a zero divisor with ANSI off;
- RoundBase (round), Ceil and Floor: mathExpressions.scala, branch-3.5 and master alike.

The expected value is Python's exact decimal arithmetic, rounded half up as Spark rounds. Where the
engine still differs from Spark, the case is listed in docs/sql-compatibility.md under LOC-005; the
last tests here check that each such case is listed and that the static check warns.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext
from itertools import product
from pathlib import Path

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel
from fabric_desktop.spark_decimals import spark_decimal_types
from fabric_desktop.sqlcompat.analyzer import analyze

DOCS = Path(__file__).resolve().parents[2] / "docs" / "sql-compatibility.md"
MAX = 38


# ---- Spark's rules, from its source

def adjust(p: int, s: int) -> tuple[int, int]:
    if p <= MAX:
        return p, s
    whole = p - s
    return MAX, max(MAX - whole, min(s, 6))


def spark_type(op: str, a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    (p1, s1), (p2, s2) = a, b
    if op == "/":
        scale = max(6, s1 + p2 + 1)
        return adjust(p1 - s1 + s2 + scale, scale)
    if op == "*":
        return adjust(p1 + p2 + 1, s1 + s2)
    if op in ("+", "-"):
        scale = max(s1, s2)
        return adjust(max(p1 - s1, p2 - s2) + scale + 1, scale)
    scale = max(s1, s2)  # % and pmod
    return adjust(min(p1 - s1, p2 - s2) + scale, scale)


def round_type(p: int, s: int, places: int) -> tuple[int, int]:
    whole = p - s + 1
    if places < 0:
        return min(max(whole, -places + 1), MAX), 0
    scale = min(s, places)
    return min(whole + scale, MAX), scale


def ceil_type(p: int, s: int) -> tuple[int, int]:
    return (p, s) if s == 0 else (min(p - s + 1, MAX), 0)


def exact(op: str, x: Decimal, y: Decimal, scale: int) -> Decimal | None:
    with localcontext() as ctx:
        ctx.prec = 200
        if op == "/":
            value = x / y if y else None
        elif op == "*":
            value = x * y
        elif op == "+":
            value = x + y
        elif op == "-":
            value = x - y
        elif op == "%":
            value = x % y if y else None  # Python's Decimal % keeps the dividend's sign, as Java's does
        else:  # pmod
            value = None if not y else (x % y if x % y >= 0 else (x % y + y) % y)
        return None if value is None else value.quantize(Decimal(1).scaleb(-scale), ROUND_HALF_UP)


# ---- the engine

def run(conn: duckdb.DuckDBPyConnection, expression: str) -> tuple[str, object]:
    statement = spark_decimal_types(sql.parse(f"SELECT {expression} AS v FROM t")[0], conn)
    rel = conn.sql(sql.duckdb_sql(statement))
    return str(rel.types[0]), rel.fetchall()[0][0]


TYPES = [(10, 2), (5, 1), (18, 6), (20, 0), (3, 0), (12, 4)]


def value_for(kind: tuple[int, int], sign: int) -> Decimal:
    p, s = kind
    whole = min(p - s, 3)
    digits = Decimal("7" * whole or "0") + (Decimal("0." + "3" * s) if s else 0)
    return sign * digits


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    columns = []
    for i, kind in enumerate(TYPES):
        columns.append(f"CAST('{value_for(kind, 1)}' AS DECIMAL{kind}) AS x{i}")
        columns.append(f"CAST('{value_for(kind, -1)}' AS DECIMAL{kind}) AS y{i}")
    columns.append("CAST(0 AS DECIMAL(10,2)) AS zero")
    c.execute(f"CREATE TEMP VIEW t AS SELECT {', '.join(columns)}")
    return c


PAIRS = [(i, j) for i, j in product(range(len(TYPES)), repeat=2)]


@pytest.mark.parametrize("op", ["/", "*", "+", "-", "%", "pmod"])
@pytest.mark.parametrize("i, j", PAIRS, ids=[f"{TYPES[i]}-{TYPES[j]}" for i, j in PAIRS])
def test_each_operator_gives_sparks_type_and_value(conn, op, i, j):
    left, right = f"x{i}", f"y{j}"
    expression = f"pmod({left}, {right})" if op == "pmod" else f"{left} {op} {right}"
    p, s = spark_type(op, TYPES[i], TYPES[j])
    kind, value = run(conn, expression)
    assert kind == f"DECIMAL({p},{s})", expression
    assert value == exact(op, value_for(TYPES[i], 1), value_for(TYPES[j], -1), s), expression


@pytest.mark.parametrize("op", ["/", "%", "pmod"])
def test_a_zero_divisor_is_null_as_spark_gives_with_ansi_off(conn, op):
    expression = "pmod(x0, zero)" if op == "pmod" else f"x0 {op} zero"
    assert run(conn, expression)[1] is None


@pytest.mark.parametrize("i", range(len(TYPES)), ids=[str(k) for k in TYPES])
def test_avg_gives_sparks_type_and_value(conn, i):
    p, s = TYPES[i]
    kind, value = run(conn, f"avg(x{i})")
    assert kind == f"DECIMAL({min(MAX, p + 4)},{min(MAX, s + 4)})"
    assert value == value_for(TYPES[i], 1).quantize(Decimal(1).scaleb(-(s + 4)))


def test_avg_of_several_rows_rounds_half_up_at_sparks_scale():
    c = duckdb.connect()
    c.execute("CREATE TEMP VIEW t AS SELECT CAST(v AS DECIMAL(10,2)) AS a FROM (VALUES (1), (2), (2)) r(v)")
    assert run(c, "avg(a)") == ("DECIMAL(14,6)", Decimal("1.666667"))


@pytest.mark.parametrize("left, right, quotient", [("7.9", "2", 3), ("-7.9", "2", -3), ("7", "-2.5", -2),
                                                   ("0.5", "3", 0), ("123456789012.34", "0.01", 12345678901234)])
def test_div_is_a_bigint_truncated_toward_zero(left, right, quotient):
    c = duckdb.connect()
    c.execute(f"CREATE TEMP VIEW t AS SELECT CAST('{left}' AS DECIMAL(14,2)) AS a, CAST('{right}' AS DECIMAL(5,2)) AS b, "
              "CAST(0 AS DECIMAL(5,2)) AS z")
    assert run(c, "a div b") == ("BIGINT", quotient)  # DuckDB's own // of a decimal is a DOUBLE division
    assert run(c, "a div z") == ("BIGINT", None)


def test_div_of_integers_is_a_bigint():
    c = duckdb.connect()
    c.execute("CREATE TEMP VIEW t AS SELECT 7::INTEGER AS n, 0::INTEGER AS z")
    assert run(c, "n div 2") == ("BIGINT", 3)
    assert run(c, "-n div 2") == ("BIGINT", -3)
    assert run(c, "n div z") == ("BIGINT", None)  # DuckDB 2.0's own // raises on a zero divisor


def test_div_past_38_digits_still_truncates_toward_zero():
    # Worked out through DOUBLE (listed in the compatibility docs); a negative quotient shows TRUNC, not FLOOR.
    c = duckdb.connect()
    c.execute("CREATE TEMP VIEW t AS SELECT CAST(-7 AS DECIMAL(38,0)) AS a, CAST(2.5 AS DECIMAL(3,2)) AS b, "
              "CAST(-1e37 AS DECIMAL(38,0)) AS big")
    assert run(c, "a div b") == ("BIGINT", -2)
    assert run(c, "big div b") == ("BIGINT", None)  # past BIGINT: NULL here, listed in the compatibility docs


ROUNDS = [(10, 2, 1), (10, 2, 2), (10, 2, 5), (10, 2, 0), (10, 2, -1), (10, 2, -3), (23, 13, 2), (5, 0, 0),
          (5, 0, -2), (38, 18, 4), (2, 0, -3)]  # (2,0) to -3: 1 - places beats p - s + 1, decimal(4,0)


@pytest.mark.parametrize("p, s, places", ROUNDS, ids=[f"{p},{s} to {d}" for p, s, d in ROUNDS])
def test_round_gives_sparks_type_and_rounds_half_up(p, s, places):
    c = duckdb.connect()
    whole = min(p - s, 4)
    text = "-" + "8765"[:whole] + ("." + "5" * s if s else "")
    c.execute(f"CREATE TEMP VIEW t AS SELECT CAST('{text}' AS DECIMAL({p},{s})) AS a")
    rp, rs = round_type(p, s, places)
    kind, value = run(c, f"round(a, {places})")
    assert kind == f"DECIMAL({rp},{rs})"
    assert value == Decimal(text).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)


@pytest.mark.parametrize("p, s, places", ROUNDS, ids=[f"{p},{s} to {d}" for p, s, d in ROUNDS])
def test_bround_gives_rounds_type_and_rounds_half_even(p, s, places):
    c = duckdb.connect()
    whole = min(p - s, 4)
    text = "-" + "8765"[:whole] + ("." + "5" * s if s else "")
    c.execute(f"CREATE TEMP VIEW t AS SELECT CAST('{text}' AS DECIMAL({p},{s})) AS a")
    rp, rs = round_type(p, s, places)
    kind, value = run(c, f"bround(a, {places})")
    assert kind == f"DECIMAL({rp},{rs})"
    assert value == Decimal(text).quantize(Decimal(1).scaleb(-places), ROUND_HALF_EVEN)


@pytest.mark.parametrize("fn, mode", [("ceil", ROUND_CEILING), ("floor", ROUND_FLOOR)])
@pytest.mark.parametrize("p, s", [(10, 2), (23, 13), (5, 0), (38, 18)])
@pytest.mark.parametrize("sign", [1, -1])
def test_ceil_and_floor_give_sparks_type(fn, mode, p, s, sign):
    c = duckdb.connect()
    text = ("-" if sign < 0 else "") + "8765"[:min(p - s, 4)] + ("." + "25" * s)[: s + 1 if s else 0]
    c.execute(f"CREATE TEMP VIEW t AS SELECT CAST('{text}' AS DECIMAL({p},{s})) AS a")
    cp, cs = ceil_type(p, s)
    assert run(c, f"{fn}(a)") == (f"DECIMAL({cp},{cs})", Decimal(text).quantize(Decimal(1), mode))


@pytest.mark.parametrize("fn, mode", [("ceil", ROUND_CEILING), ("floor", ROUND_FLOOR)])
@pytest.mark.parametrize("places", [2, 1, 0, -1, -2, 5])
@pytest.mark.parametrize("text", ["1234.5678", "-1234.5678", "0.0001"])
def test_ceil_and_floor_to_places_are_exact_decimals(fn, mode, places, text):
    c = duckdb.connect()
    c.execute(f"CREATE TEMP VIEW t AS SELECT CAST('{text}' AS DECIMAL(12,4)) AS a")
    rp, rs = round_type(12, 4, places)
    kind, value = run(c, f"{fn}(a, {places})")
    assert kind == f"DECIMAL({rp},{rs})"  # sqlglot's own translation goes through POWER, a DOUBLE
    assert value == Decimal(text).quantize(Decimal(1).scaleb(-places), mode).quantize(Decimal(1).scaleb(-rs))


def test_the_live_comparisons_decimal_division_now_matches_spark():
    """The one query that differed in the 2026-11-03 Fabric comparison (docs/platforms.md): Spark gave
    decimal(14,6) 0.333333. That comparison ran DuckDB directly; this is the local spark.sql."""
    out = Kernel().run("r = spark.sql('SELECT CAST(1 AS DECIMAL(10,2)) / 3 AS q')\n"
                       "print(r.dtypes, [tuple(x) for x in r.collect()])")
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    assert "[('q', 'decimal(14,6)')] [(Decimal('0.333333'),)]" in text


def test_division_multiplication_and_avg_together_in_one_query_through_spark_sql():
    out = Kernel().run("spark.createDataFrame([(1,)], 'i int').selectExpr("
                       "'CAST(1234.56 AS DECIMAL(12,2)) AS price', 'CAST(3 AS INT) AS qty', "
                       "'CAST(0.0725 AS DECIMAL(5,4)) AS rate').createOrReplaceTempView('o')\n"
                       "r = spark.sql('SELECT price * qty AS total, price * rate AS tax, price / qty AS each, "
                       "avg(price) AS mean, price % 7 AS rest, price div 7 AS whole, round(price / qty, 1) AS r1 "
                       "FROM o GROUP BY price, qty, rate')\n"
                       "print(r.dtypes)\nprint([tuple(x) for x in r.collect()])")
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    assert ("[('total', 'decimal(23,2)'), ('tax', 'decimal(18,6)'), ('each', 'decimal(23,13)'), "
            "('mean', 'decimal(16,6)'), ('rest', 'decimal(3,2)'), ('whole', 'bigint'), ('r1', 'decimal(12,1)')]") in text
    assert ("(Decimal('3703.68'), Decimal('89.505600'), Decimal('411.5200000000000'), Decimal('1234.560000'), "
            "Decimal('2.56'), 176, Decimal('411.5'))") in text


# ---- what still differs is listed and warned about

STILL_DIFFERS = {
    "product past 38 digits": "SELECT CAST(1.5 AS DECIMAL(38,18)) * CAST(1.5 AS DECIMAL(38,18)) AS v FROM t",
    "division whose dividend": "SELECT CAST(x AS DECIMAL(38,18)) / CAST(y AS DECIMAL(38,18)) AS v FROM t",
}


def test_what_still_differs_is_listed_in_the_compatibility_docs():
    text = DOCS.read_text(encoding="utf-8")
    section = text.split("## Decimal results on this PC (LOC-005)", 1)[1].split("\n## ", 1)[0]
    for words in STILL_DIFFERS:
        assert words in section.lower(), words


def test_a_product_past_38_digits_keeps_duckdbs_type_as_listed():
    c = duckdb.connect()
    c.execute("CREATE TEMP VIEW t AS SELECT 1")
    kind, value = run(c, "CAST(1.5 AS DECIMAL(38,18)) * CAST(1.5 AS DECIMAL(38,18))")
    assert (kind, value) == ("DECIMAL(38,36)", Decimal("2.25"))  # Spark: decimal(38,6) 2.250000
    p, s = spark_type("*", (38, 18), (38, 18))
    assert (p, s) == (38, 6) and kind != f"DECIMAL({p},{s})"


@pytest.mark.parametrize("case", list(STILL_DIFFERS))
def test_the_static_check_warns_where_results_can_still_differ(case):
    hits = [d for d in analyze(STILL_DIFFERS[case], "spark")["diagnostics"] if d["rule"] == "LOC-005"]
    assert hits, case


@pytest.mark.parametrize("fn, expected", [("ceil", "-123.45"), ("floor", "-123.46")])
def test_ceil_and_floor_to_places_at_exactly_38_digits_stay_exact(fn, expected):
    # decimal(36,3) to 2 places: p + places is 38, which still fits.
    c = duckdb.connect()
    c.execute("CREATE TEMP VIEW t AS SELECT CAST('-123.456' AS DECIMAL(36,3)) AS a")
    rp, rs = round_type(36, 3, 2)
    assert run(c, f"{fn}(a, 2)") == (f"DECIMAL({rp},{rs})", Decimal(expected))

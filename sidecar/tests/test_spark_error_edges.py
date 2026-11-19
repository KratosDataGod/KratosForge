"""Spark SQL calls that stopped here where Spark answers, found by the differential test
(tests/spark_differential.py). Each answer is Apache Spark 3.5.3's (local, UTC, ANSI off): initcap of any
argument, soundex of a rewritten argument, trunc and make_date inside another call, sqrt of a negative
number (NaN, as Java's Math.sqrt), and sign of NaN and -0.0 (themselves, as Java's Math.signum)."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from fabric_desktop import sql


def one(expression: str):
    return next(iter(sql.run("SELECT " + expression).to_pylist()[0].values()))


@pytest.mark.parametrize(("expression", "spark"), [
    ("initcap('aaa')", "Aaa"),
    ("initcap(upper('MiXeD'))", "Mixed"),
    ("initcap('  hello  wORLD ')", "  Hello  World "),
    ("initcap('ab-cd éf')", "Ab-cd Éf"),
    ("initcap(CAST(DATE'2023-12-31' AS STRING))", "2023-12-31"),
    ("initcap(CAST(NULL AS STRING))", None),
    ("soundex(CAST(DATE'2000-01-01' AS STRING))", "2000-01-01"),
    ("soundex(translate(CAST(10.005 AS STRING), ',', 'A'))", "10.005"),
    ("trunc(trunc(DATE'2024-03-01', 'MM'), 'MONTH')", dt.date(2024, 3, 1)),
    ("trunc(last_day(trunc(DATE'2023-12-31', 'yyyy')), 'WEEK')", dt.date(2023, 1, 30)),
    ("date_add(trunc(DATE'2024-03-01', 'yyyy'), 1)", dt.date(2024, 1, 2)),
    ("next_day(make_date(1900, -1, -1), 'MON')", None),
    ("date_add(last_day(make_date(2024, -1, 3)), -100)", None),
    ("date_sub(greatest((DATE'2000-01-01' + -6), make_date(2023, 100, 7)), 8)", dt.date(1999, 12, 18)),
    ("sqrt(4)", 2.0),
    ("sqrt(CAST(NULL AS DOUBLE))", None),
    ("isnan(sqrt(-2.5D))", True),
    ("string(sign(sqrt(-3.75D)))", "NaN"),
    ("sign(-3)", -1.0),
    ("string(sign(-0.0D))", "-0.0"),
    ("trim(string(sign((pmod(-2.5D, -2.5D)))))", "-0.0"),
    ("string(sign(0))", "0.0"),
    ("sign(2.5)", 1.0),
    ("sign(CAST(NULL AS INT))", None),
])
def test_answers_as_spark(expression: str, spark) -> None:
    assert one(expression) == spark


def test_sqrt_of_a_negative_number_is_nan() -> None:
    assert math.isnan(one("sqrt(-2.5D)")) and math.isnan(one("round(sqrt(-2.5D), 1)"))


def test_initcap_of_a_column() -> None:
    assert sql.run("SELECT initcap(x) AS v FROM (SELECT 'ab cd' AS x)").to_pylist() == [{"v": "Ab Cd"}]


def test_a_kf_name_of_the_persons_own_is_still_refused() -> None:
    with pytest.raises(ValueError, match="kf_x can't be used"):
        sql.run("SELECT soundex(kf_x) FROM (SELECT 'a' AS kf_x)")


def test_sqrt_and_sign_read_a_changing_argument_once() -> None:
    # The check and the root (or sign) must see the same sample: read twice, sqrt(rand() - 0.5) stopped with
    # "cannot take square root of a negative number" when the check saw a positive sample and SQRT a negative one.
    rows = sql.run("SELECT sqrt(rand() - 0.5) AS r, sign(rand() - 0.5) AS s FROM range(20000)").to_pylist()
    assert len(rows) == 20000
    assert {row["s"] for row in rows} <= {1.0, -1.0}
    assert all(math.isnan(row["r"]) or row["r"] >= 0 for row in rows)

"""Capture Spark 3.5's answers to generated expressions for tests/test_spark_differential.py.

Differential testing: a seeded generator writes random scalar Spark SQL expressions from the functions
KratosForge's local Spark SQL claims to run, real Spark answers each one, and the test asks the local
engine the same question. A different answer is a silent wrong answer unless the test lists it as a
documented difference; an error is loud and is counted, not failed.

Needs PySpark 3.5 and Java, and is run by hand, never in CI (as tools/capture_spark_builtins.py is):

    python -m venv spark && spark/bin/pip install pyspark==3.5.3
    spark/bin/python tools/capture_spark_differential.py [--seed 20261117] [--count 1500]

The expressions in tools/spark_differential_pinned.txt are added to every capture: each one answered
differently here in an earlier, larger run, so a fix keeps its case after the generator changes.

Spark runs in local mode, UTC, ANSI mode off: how Fabric runs Spark SQL (docs/sql-compatibility.md).
Expressions avoid the clock, random numbers and the session, which can't be compared.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "spark_3_5_3_differential.json"
PINNED = Path(__file__).resolve().with_name("spark_differential_pinned.txt")

STRINGS = ["''", "'a'", "'abc'", "'Hello World'", "'  pad  '", "'a,b,,c'", "'ÄÖü'", "'x_y%z'", "'aaa'",
           "'MiXeD'", "' '", "'12'", "'-7'", "'3.5'", "'2024-02-29'", "'abcABC'"]
CHARS = ["'a'", "'b'", "','", "' '", "'_'", "'%'", "'A'", "''", "'ab'"]
INTS = ["0", "1", "-1", "2", "3", "-3", "7", "-7", "10", "100", "-100", "255", "1000000"]
SMALL = ["0", "1", "2", "3", "-1", "-2", "4", "5", "6", "-4", "-6", "8"]
DECIMALS = ["0.5", "-0.5", "1.25", "-1.25", "2.5", "-2.5", "3.14159", "10.005", "-10.005", "123.456", "0.0",
            "99.99", "1.5", "-1.5", "0.049"]
DOUBLES = ["0.5D", "-0.5D", "2.5D", "-2.5D", "1.0E3D", "3.75D", "-3.75D", "0.0D", "1.0E-3D", "12.5D"]
DATES = ["DATE'2024-01-31'", "DATE'2024-02-29'", "DATE'2023-12-31'", "DATE'2024-03-01'", "DATE'2000-01-01'",
         "DATE'2024-06-15'", "DATE'1999-12-31'", "DATE'2024-01-06'", "DATE'2024-01-07'", "DATE'2025-02-28'"]
UNITS = ["YEAR", "QUARTER", "MONTH", "WEEK", "DAY"]
TRUNCS = ["'YEAR'", "'MM'", "'MONTH'", "'QUARTER'", "'WEEK'", "'yyyy'"]
LIKES = ["'a%'", "'%c'", "'_b_'", "'%'", "''", "'a\\_b'", "'%o W%'", "'A%'", "'__'", "'%,%'"]
NULLS = {"int": "CAST(NULL AS INT)", "str": "CAST(NULL AS STRING)", "dec": "CAST(NULL AS DECIMAL(10,3))",
         "dbl": "CAST(NULL AS DOUBLE)", "date": "CAST(NULL AS DATE)", "bool": "CAST(NULL AS BOOLEAN)"}


class Gen:
    """Random, well-typed Spark SQL expressions. ``depth`` caps nesting so each one stays readable."""

    def __init__(self, seed: int) -> None:
        self.r = random.Random(seed)

    def pick(self, items: list[str]) -> str:
        return self.r.choice(items)

    def leaf(self, kind: str) -> str:
        if self.r.random() < 0.07:
            return NULLS[kind]
        return {"int": lambda: self.pick(INTS), "str": lambda: self.pick(STRINGS),
                "dec": lambda: self.pick(DECIMALS), "dbl": lambda: self.pick(DOUBLES),
                "date": lambda: self.pick(DATES),
                "bool": lambda: self.pick(["true", "false"])}[kind]()

    def small(self) -> str:
        return NULLS["int"] if self.r.random() < 0.04 else self.pick(SMALL)

    def expr(self, kind: str, depth: int) -> str:
        if depth <= 0 or self.r.random() < 0.25:
            return self.leaf(kind)
        return self.r.choice(getattr(self, "f_" + kind))(depth - 1)

    # ---- whole numbers
    @property
    def f_int(self):
        e, s = self.expr, self.small
        return [
            lambda d: f"({e('int', d)} {self.pick(['+', '-', '*'])} {e('int', d)})",
            lambda d: f"({e('int', d)} div {e('int', d)})",
            lambda d: f"({e('int', d)} % {e('int', d)})",
            lambda d: f"pmod({e('int', d)}, {e('int', d)})",
            lambda d: f"abs({e('int', d)})",
            lambda d: f"(- {e('int', d)})",
            lambda d: f"length({e('str', d)})",
            lambda d: f"char_length({e('str', d)})",
            lambda d: f"instr({e('str', d)}, {self.pick(CHARS)})",
            lambda d: f"locate({self.pick(CHARS)}, {e('str', d)}, {s()})",
            lambda d: f"ascii({e('str', d)})",
            lambda d: f"CAST({e('str', d)} AS INT)",
            lambda d: f"CAST({e('dec', d)} AS INT)",
            lambda d: f"CAST({e('dbl', d)} AS BIGINT)",
            lambda d: f"floor({e('dec', d)})",
            lambda d: f"ceil({e('dbl', d)})",
            lambda d: f"sign({e('int', d)})",
            lambda d: f"greatest({e('int', d)}, {e('int', d)}, {e('int', d)})",
            lambda d: f"least({e('int', d)}, {e('int', d)})",
            lambda d: f"coalesce({e('int', d)}, {e('int', d)})",
            lambda d: f"nullif({e('int', d)}, {e('int', d)})",
            lambda d: f"if({e('bool', d)}, {e('int', d)}, {e('int', d)})",
            lambda d: f"CASE WHEN {e('bool', d)} THEN {e('int', d)} WHEN {e('bool', d)} THEN {e('int', d)} END",
            lambda d: f"({e('int', d)} {self.pick(['&', '|', '^'])} {e('int', d)})",
            lambda d: f"shiftleft({e('int', d)}, {s()})",
            lambda d: f"shiftright({e('int', d)}, {s()})",
            lambda d: f"year({e('date', d)})",
            lambda d: f"month({e('date', d)})",
            lambda d: f"dayofmonth({e('date', d)})",
            lambda d: f"dayofweek({e('date', d)})",
            lambda d: f"weekday({e('date', d)})",
            lambda d: f"dayofyear({e('date', d)})",
            lambda d: f"weekofyear({e('date', d)})",
            lambda d: f"quarter({e('date', d)})",
            lambda d: f"datediff({e('date', d)}, {e('date', d)})",
            lambda d: f"timestampdiff({self.pick(UNITS)}, {e('date', d)}, {e('date', d)})",
            lambda d: f"size(split({e('str', d)}, {self.pick(CHARS)}))",
            lambda d: f"round({e('int', d)}, {self.pick(['-1', '-2', '0', '1'])})",
            lambda d: f"bit_count({e('int', d)})",
            lambda d: f"find_in_set({self.pick(CHARS)}, {e('str', d)})",
            lambda d: f"levenshtein({e('str', d)}, {e('str', d)})",
        ]

    # ---- text
    @property
    def f_str(self):
        e, s = self.expr, self.small
        return [
            lambda d: f"substring({e('str', d)}, {s()}, {s()})",
            lambda d: f"substring({e('str', d)}, {s()})",
            lambda d: f"substr({e('str', d)}, {s()}, {s()})",
            lambda d: f"left({e('str', d)}, {s()})",
            lambda d: f"right({e('str', d)}, {s()})",
            lambda d: f"concat({e('str', d)}, {e('str', d)})",
            lambda d: f"concat_ws({self.pick(CHARS)}, {e('str', d)}, {e('str', d)}, {e('str', d)})",
            lambda d: f"({e('str', d)} || {e('str', d)})",
            lambda d: f"upper({e('str', d)})",
            lambda d: f"lower({e('str', d)})",
            lambda d: f"initcap({e('str', d)})",
            lambda d: f"trim({e('str', d)})",
            lambda d: f"ltrim({e('str', d)})",
            lambda d: f"rtrim({e('str', d)})",
            lambda d: f"trim(BOTH {self.pick(CHARS)} FROM {e('str', d)})",
            lambda d: f"lpad({e('str', d)}, {s()}, {self.pick(CHARS)})",
            lambda d: f"rpad({e('str', d)}, {s()}, {self.pick(CHARS)})",
            lambda d: f"repeat({e('str', d)}, {s()})",
            lambda d: f"reverse({e('str', d)})",
            lambda d: f"replace({e('str', d)}, {self.pick(CHARS)}, {self.pick(CHARS)})",
            lambda d: f"translate({e('str', d)}, {self.pick(CHARS)}, {self.pick(CHARS)})",
            lambda d: f"overlay({e('str', d)} PLACING {self.pick(CHARS)} FROM {s()})",
            lambda d: f"split_part({e('str', d)}, {self.pick(CHARS)}, {s()})",
            lambda d: f"substring_index({e('str', d)}, {self.pick(CHARS)}, {s()})",
            lambda d: f"nvl({e('str', d)}, {e('str', d)})",
            lambda d: f"if({e('bool', d)}, {e('str', d)}, {e('str', d)})",
            lambda d: f"CAST({e('int', d)} AS STRING)",
            lambda d: f"CAST({e('dec', d)} AS STRING)",
            lambda d: f"CAST({e('date', d)} AS STRING)",
            lambda d: f"CAST({e('bool', d)} AS STRING)",
            lambda d: f"date_format({e('date', d)}, {self.pick(["'yyyy-MM-dd'", "'dd/MM/yyyy'", "'MMM d, yyyy'", "'EEEE'", "'yyyyMM'", "'D'"])})",
            lambda d: f"format_number({e('dec', d)}, {self.pick(['0', '1', '2'])})",
            lambda d: f"regexp_replace({e('str', d)}, {self.pick(["'[a-c]'", "'\\\\s+'", "'^.'", "'[0-9]'"])}, {self.pick(CHARS)})",
            lambda d: f"regexp_extract({e('str', d)}, {self.pick(["'([a-z]+)'", "'(\\\\d+)'", "'([A-Z])([a-z]*)'"])}, {self.pick(['0', '1'])})",
            lambda d: f"soundex({e('str', d)})",
            lambda d: f"string(sign({e('dbl', d)}))",
        ]

    # ---- exact decimals
    @property
    def f_dec(self):
        e = self.expr
        return [
            lambda d: f"({e('dec', d)} {self.pick(['+', '-'])} {e('dec', d)})",
            lambda d: f"({e('dec', d)} * {e('int', d)})",
            lambda d: f"round({e('dec', d)}, {self.pick(['-1', '0', '1', '2'])})",
            lambda d: f"bround({e('dec', d)}, {self.pick(['-1', '0', '1', '2'])})",
            lambda d: f"abs({e('dec', d)})",
            lambda d: f"CAST({e('str', d)} AS DECIMAL(10,3))",
            lambda d: f"CAST({e('int', d)} AS DECIMAL(10,3))",
            lambda d: f"coalesce({e('dec', d)}, {e('dec', d)})",
            lambda d: f"greatest({e('dec', d)}, {e('dec', d)})",
        ]

    # ---- doubles (compared with a relative tolerance)
    @property
    def f_dbl(self):
        e = self.expr
        return [
            lambda d: f"({e('int', d)} / {e('int', d)})",
            lambda d: f"({e('dbl', d)} {self.pick(['+', '-', '*'])} {e('dbl', d)})",
            lambda d: f"({e('dbl', d)} / {e('dbl', d)})",
            lambda d: f"round({e('dbl', d)}, {self.pick(['0', '1'])})",
            lambda d: f"bround({e('dbl', d)}, {self.pick(['0', '1'])})",
            lambda d: f"CAST({e('str', d)} AS DOUBLE)",
            lambda d: f"CAST({e('int', d)} AS DOUBLE)",
            lambda d: f"power({e('int', d)}, {self.pick(['0', '1', '2', '-1'])})",
            lambda d: f"sqrt({e('dbl', d)})",
            lambda d: f"months_between({e('date', d)}, {e('date', d)})",
            lambda d: f"(pmod({e('dbl', d)}, {e('dbl', d)}))",
            lambda d: f"nanvl({e('dbl', d)}, {e('dbl', d)})",
        ]

    # ---- dates
    @property
    def f_date(self):
        e, s = self.expr, self.small
        return [
            lambda d: f"date_add({e('date', d)}, {e('int', d)})",
            lambda d: f"date_sub({e('date', d)}, {s()})",
            lambda d: f"add_months({e('date', d)}, {e('int', d)})",
            lambda d: f"last_day({e('date', d)})",
            lambda d: f"trunc({e('date', d)}, {self.pick(TRUNCS)})",
            lambda d: f"next_day({e('date', d)}, {self.pick(["'MON'", "'Sunday'", "'fri'"])})",
            lambda d: f"make_date({self.pick(['2024', '2023', '1900'])}, {e('int', d)}, {e('int', d)})",
            lambda d: f"to_date({e('str', d)})",
            lambda d: f"to_date({e('str', d)}, {self.pick(["'yyyy-MM-dd'", "'yyyyMMdd'", "'dd/MM/yyyy'"])})",
            lambda d: f"CAST({e('str', d)} AS DATE)",
            lambda d: f"greatest({e('date', d)}, {e('date', d)})",
            lambda d: f"coalesce({e('date', d)}, {e('date', d)})",
            lambda d: f"({e('date', d)} + {s()})",
        ]

    # ---- three-valued logic
    @property
    def f_bool(self):
        e = self.expr
        cmp = ["=", "<>", "<", "<=", ">", ">=", "<=>"]
        return [
            lambda d: f"({e('int', d)} {self.pick(cmp)} {e('int', d)})",
            lambda d: f"({e('str', d)} {self.pick(cmp)} {e('str', d)})",
            lambda d: f"({e('dec', d)} {self.pick(cmp)} {e('int', d)})",
            lambda d: f"({e('date', d)} {self.pick(cmp)} {e('date', d)})",
            lambda d: f"({e('bool', d)} {self.pick(['AND', 'OR'])} {e('bool', d)})",
            lambda d: f"(NOT {e('bool', d)})",
            lambda d: f"({e('int', d)} IS NULL)",
            lambda d: f"({e('str', d)} LIKE {self.pick(LIKES)})",
            lambda d: f"({e('str', d)} ILIKE {self.pick(LIKES)})",
            lambda d: f"({e('int', d)} IN ({e('int', d)}, {e('int', d)}, {e('int', d)}))",
            lambda d: f"({e('int', d)} NOT IN ({e('int', d)}, {e('int', d)}))",
            lambda d: f"({e('int', d)} BETWEEN {e('int', d)} AND {e('int', d)})",
            lambda d: f"startswith({e('str', d)}, {self.pick(CHARS)})",
            lambda d: f"endswith({e('str', d)}, {self.pick(CHARS)})",
            lambda d: f"contains({e('str', d)}, {self.pick(CHARS)})",
            lambda d: f"({e('str', d)} RLIKE {self.pick(["'^[a-z]+$'", "'\\\\d'", "'b'"])})",
            lambda d: f"isnan({e('dbl', d)})",
            lambda d: f"CAST({e('str', d)} AS BOOLEAN)",
        ]

    def one(self) -> str:
        kind = self.r.choices(["int", "str", "dec", "dbl", "date", "bool"], weights=[5, 5, 2, 2, 3, 3])[0]
        return self.expr(kind, self.r.choice([1, 2, 2, 3]))


def generate(seed: int, count: int) -> list[str]:
    g, seen, out = Gen(seed), set(), []
    while len(out) < count:
        q = g.one()
        if q not in seen and not q.startswith(("CAST(NULL", "'", "DATE'")) and q not in INTS + DOUBLES + DECIMALS:
            seen.add(q)
            out.append(q)
    return out


def encode(value: object) -> object:
    """A value as JSON keeps it: decimals and dates as tagged text, so nothing is rounded on the way."""
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, dt.datetime):
        return {"timestamp": value.isoformat()}
    if isinstance(value, dt.date):
        return {"date": value.isoformat()}
    if isinstance(value, float) and value != value:
        return {"double": "NaN"}
    if isinstance(value, float) and value in (float("inf"), float("-inf")):
        return {"double": str(value)}
    return value


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261117)
    ap.add_argument("--count", type=int, default=1500)
    args = ap.parse_args()

    from pyspark.sql import SparkSession

    spark = (SparkSession.builder.master("local[1]").config("spark.ui.enabled", "false")
             .config("spark.sql.session.timeZone", "UTC").config("spark.sql.ansi.enabled", "false").getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    cases = []
    pinned = [line.strip() for line in PINNED.read_text(encoding="utf-8").splitlines()
              if line.strip() and not line.startswith("#")]
    generated = generate(args.seed, args.count)
    for q in generated + [p for p in dict.fromkeys(pinned) if p not in generated]:
        try:
            row = spark.sql(f"SELECT {q} AS v, typeof({q}) AS t").collect()[0]
            cases.append({"sql": q, "spark": encode(row[0]), "type": row[1]})
        except Exception as exc:  # noqa: BLE001 - silent-ok: Spark's own refusal is the recorded answer
            cases.append({"sql": q, "spark": None, "error": type(exc).__name__})
    OUT.write_text(json.dumps({"spark": spark.version, "ansi": False, "time_zone": "UTC", "seed": args.seed,
                               "cases": cases}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(cases)} cases, {sum('error' in c for c in cases)} Spark errors -> {OUT}")


if __name__ == "__main__":
    main()

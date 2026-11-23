"""Fuzz the readers of DAX measure text from a person's semantic model: the converter's DAX-to-SQL translation of
single-column aggregates (``_vendor/ossie_microsoft/_dax_to_sql``, which model_ossie.export runs with translate_dax),
and the L2 and L3 parity readers (semantic_parity._side_form, semantic_parity_l3.parity_l3).

The properties: a measure is translated only when it is one of the six aggregates over one fully qualified column,
to exactly that aggregate, and the SQL computes what the DAX does on small data (run here: the DAX by its documented
rules, the SQL on DuckDB). Everything else is refused, never a different number. The parity readers take any text and
any pair of models, and refuse what is outside their subset as a reason, never another exception, in time linear in
the text. No DAX text reaches the SQL L3 runs except as a quoted name. Every loop uses fixed seeds, so a failure names
its seed and reproduces exactly."""

from __future__ import annotations

import gc
import logging
import random
import re
import time
from decimal import Decimal

import duckdb
import pytest

from fabric_desktop import semantic_parity as sp, semantic_parity_l3 as l3
from fabric_desktop._vendor.ossie_microsoft._dax_to_sql import translate_measure
from test_semantic_parity_l2 import _model, _ossie

KNOWN = {("Sales", "Amount"), ("Sales", "Qty"), ("dim_x", "k_1"), ("Sales Data", "Amount"), ("T", "c]d"),
         ("O'Neil", "x"), ("t", "order")}
FUNCTIONS = ["SUM", "AVERAGE", "MIN", "MAX", "COUNTA", "DISTINCTCOUNTNOBLANK", "COUNT", "DISTINCTCOUNT", "COUNTROWS",
             "SUMX", "CALCULATE", "sum", "Average", "DIVIDE", "IF", "MEDIAN", "SUM.X"]
SPACE = ["", " ", "\t", "\n", "\r\n", "\u00a0", "\u3000"]
PIECES = ["(", ")", "[", "]", "'", "''", "]]", ",", "/", "+", "--", "//", "/*", "*/", '"', "=", " ", "\n", "Sales",
          "Amount", "SUM(", "VAR x = ", "RETURN ", "\x00", "\u202e", "\u200b", "\u00e9", "0", "1e5", ".", "BLANK()", "{", ";"]
SQL_AGGREGATE = {"SUM": "SUM", "AVERAGE": "AVG", "MIN": "MIN", "MAX": "MAX", "COUNTA": "COUNT",
                 "DISTINCTCOUNTNOBLANK": "COUNT(DISTINCT"}
# What a translatable measure looks like, written independently of the converter: one of the six aggregates, a
# quoted or plain table, one plain column. The converter must translate exactly these (names it knows) and no more.
CANONICAL = re.compile(r"\s*(?:=\s*)?([A-Za-z]+)\s*\(\s*(?:'((?:[^']|'')*)'|([A-Za-z_][A-Za-z0-9_]*))\s*"
                       r"\[([A-Za-z_][A-Za-z0-9_]*)\]\s*\)\s*")


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _resolve(table: str, column: str):
    return (table, column) if (table, column) in KNOWN else None


def _generated(rnd: random.Random) -> str:
    table, column = rnd.choice(sorted(KNOWN))
    quoted = rnd.random() < .5 or not re.fullmatch(r"[A-Za-z_]\w*", table, re.ASCII)
    ref = ("'" + table.replace("'", "''") + "'" if quoted else table) + rnd.choice(SPACE) + "[" + column.replace("]", "]]") + "]"
    return (rnd.choice(["", "=", " = "]) + rnd.choice(FUNCTIONS) + rnd.choice(SPACE) + "(" + rnd.choice(SPACE) + ref
            + rnd.choice(SPACE) + ")")


def _mutated(rnd: random.Random, text: str) -> str:
    chars = list(text)
    for _ in range(rnd.randint(1, 4)):
        at = rnd.randrange(len(chars) + 1)
        roll = rnd.random()
        if roll < .4:
            chars[at:at] = list(rnd.choice(PIECES))
        elif chars and roll < .7:
            del chars[min(at, len(chars) - 1)]
        elif chars:
            chars[min(at, len(chars) - 1)] = chr(rnd.choice([rnd.randrange(32, 127), rnd.randrange(0x80, 0x3000)]))
    return "".join(chars)


def _case(seed: int) -> str:
    rnd = random.Random(seed)
    text = _generated(rnd)
    return _mutated(rnd, text) if rnd.random() < .6 else text


# ---- DAX to SQL, the converter's translation

@pytest.mark.parametrize("seed", range(4))
def test_only_a_single_aggregate_over_one_column_is_translated_and_to_that_aggregate(seed):
    for i in range(3000):
        text = _case(seed * 100_000 + i)
        sql, reason = translate_measure(text, _resolve)
        if sql is None:
            assert isinstance(reason, str) and reason, (seed, i, text)
            continue
        m = CANONICAL.fullmatch(text)
        assert m and m.group(1).upper() in SQL_AGGREGATE, (seed, i, text, sql)
        table = m.group(2).replace("''", "'") if m.group(2) is not None else m.group(3)
        fn = SQL_AGGREGATE[m.group(1).upper()]
        assert sql == f"{fn}{'(' if fn != 'COUNT(DISTINCT' else ' '}{table}.{m.group(4)})", (seed, i, text, sql)


def _dax(fn: str, values: list) -> object:
    """A DAX aggregate over one column by its documented rules: BLANK (None) skipped; BLANK over nothing to count."""
    present = [v for v in values if v is not None]
    if fn in ("COUNTA", "DISTINCTCOUNTNOBLANK"):
        n = len(present) if fn == "COUNTA" else len(set(present))
        return n or None  # DAX gives BLANK where SQL gives 0: the convention the converter accepts both ways
    if not present:
        return None
    return {"SUM": sum, "MIN": min, "MAX": max, "AVERAGE": lambda v: sum(v) / len(v)}[fn](present)


@pytest.mark.parametrize("seed", range(3))
def test_a_translated_measure_computes_on_small_data_what_the_dax_does(seed):
    # The translation is only worth having if the number is the same: run both sides on random small columns.
    rnd = random.Random(seed)
    con = duckdb.connect(":memory:")
    try:
        for i in range(60):
            fn = rnd.choice(sorted(SQL_AGGREGATE))
            values = [rnd.choice([None, Decimal(rnd.randint(-5, 5)), Decimal(rnd.randint(-500, 500)) / 100])
                      for _ in range(rnd.randint(0, 8))]
            sql, reason = translate_measure(f"{fn}('Sales'[Amount])", _resolve)
            assert sql is not None, reason
            con.execute("CREATE OR REPLACE TABLE Sales (Amount DECIMAL(18, 2))")
            if values:
                con.executemany("INSERT INTO Sales VALUES (?)", [[v] for v in values])
            got = con.execute(f"SELECT {sql} FROM Sales").fetchone()[0]
            want = _dax(fn, values)
            if want is None and fn in ("COUNTA", "DISTINCTCOUNTNOBLANK"):
                assert got == 0, (seed, i, fn, values)
            elif want is None or got is None:
                assert want is got is None, (seed, i, fn, values, got)
            else:
                assert abs(Decimal(str(got)) - Decimal(str(want))) < Decimal("1e-9"), (seed, i, fn, values, got, want)
    finally:
        con.close()


# ---- the parity readers

def _best_time(read, text: str) -> float:
    """CPU time for one read, the best of three samples with the garbage collector off (as test_spark_sql_fuzz)."""
    best = float("inf")
    collecting = gc.isenabled()
    gc.disable()
    try:
        for _ in range(3):
            reads, start = 0, time.process_time()
            while (spent := time.process_time() - start) < 0.03 or not reads:
                read(text)
                reads += 1
            best = min(best, spent / reads)
    finally:
        if collecting:
            gc.enable()
    return best


def _l2_form(text: str, dialect: str = "DAX") -> dict:
    side = sp._Side("left")
    return sp._side_form(side, {"expr": {"dialects": {dialect: text}}}, frozenset({"amount", "m"}))


@pytest.mark.parametrize("seed", range(3))
def test_l2_reads_any_measure_text_or_says_why_not(seed):
    for i in range(2000):
        text = _case(seed * 100_000 + 50_000 + i)
        for dialect in ("DAX", "ANSI_SQL"):
            try:
                out = _l2_form(text, dialect)
            except Exception as exc:  # noqa: BLE001 - the property under test
                pytest.fail(f"seed {seed} case {i} {dialect} {text!r}: {type(exc).__name__}: {exc}")
            assert (out["form"] is None) == bool(out["reason"]), (seed, i, text)


@pytest.mark.parametrize("motif", ["'", "]]", "' [", "SUM("])
def test_readers_take_time_linear_in_the_text(motif):
    # L2 refuses past 4,096 characters, so it is timed below that; the converter reads a measure of any length
    for read, sizes in ((lambda t: translate_measure(t, _resolve), (5_000, 20_000)), (_l2_form, (200, 800)),
                        (lambda t: _l2_form(t, "ANSI_SQL"), (200, 800))):
        small, big = (_best_time(read, "SUM(" + motif * n + ")") for n in sizes)
        assert big < max(small, 2e-5) * 12, (motif, small, big)  # four times the text: never sixteen times the time


def _pair(rnd: random.Random):
    tables = ["fact_sales", "'fact_sales'", "FACT_SALES", "dim_customer", "'fact sales'", "x", "'a\"b'", "'x;DROP'"]
    columns = ["amount", "customer_sk", "country", "nope", 'a"b', "x]]y"]

    def dax_term() -> str:
        roll = rnd.random()
        if roll < .7:
            return f"{rnd.choice(sorted(sp._DAX_AGGREGATES))}({rnd.choice(tables)}[{rnd.choice(columns)}])"
        return f"COUNTROWS({rnd.choice(tables)})" if roll < .85 else f"[{rnd.choice(['m', 'zz'])}]"

    def sql_term() -> str:
        col = rnd.choice(["amount", "customer_sk", "country", "fact_sales.amount", '"amount"', "AMOUNT", "nope",
                          '"a""b"', "fact_sales.customer_sk"])
        roll = rnd.random()
        if roll < .55:
            return f"{rnd.choice(['SUM', 'AVG', 'MIN', 'MAX', 'COUNT'])}({col})"
        return f"COUNT(DISTINCT {col})" if roll < .7 else rnd.choice(["COUNT(*)", "m", "zz", f"SUM({col} + 1)"])

    def shaped(term) -> str:
        roll = rnd.random()
        if roll < .4:
            return term()
        if roll < .8:
            return f"DIVIDE({term()}, {term()}{rnd.choice(['', ', 0', ', BLANK()', ', -1.50'])})" if term is dax_term \
                else f"{term()} / NULLIF({term()}, {rnd.choice(['0', '0.0', '1'])})"
        return f"{term()} / {term()}"
    bim = _model(shaped(dax_term))
    right = _ossie(bim, shaped(sql_term), dialect=rnd.choice(["ANSI_SQL", "OSSIE_SQL_2026", "SNOWFLAKE", "DATABRICKS"]),
                   dataset_scoped=rnd.random() < .3)
    for d in right["datasets"]:
        for f in d.get("fields") or []:
            if rnd.random() < .15:
                f["datatype"] = rnd.choice(["boolean", "string", "double", "date", "int64", "weird", None])
    return bim if rnd.random() < .5 else _ossie(bim, shaped(sql_term)), right


@pytest.mark.parametrize("seed", range(2))
def test_l3_runs_any_pair_of_measures_or_says_why_not(seed):
    rnd = random.Random(seed)
    for i in range(12):
        left, right = _pair(rnd)
        try:
            report = l3.parity_l3(left, right)
        except Exception as exc:  # noqa: BLE001 - the property under test
            pytest.fail(f"seed {seed} case {i}: {type(exc).__name__}: {exc}")
        for o in report["objects"]:
            if o["result"] == sp.VERIFIED:
                assert o["behavior"]["left"]["results"] == o["behavior"]["right"]["results"], (seed, i)

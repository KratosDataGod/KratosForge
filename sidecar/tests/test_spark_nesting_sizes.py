"""Spark functions nested in themselves stay small on this PC. Their DuckDB SQL named an argument several
times (base64 four times, pmod five, width_bucket six), so each level of nesting multiplied the SQL: six
levels of base64 were megabytes, and hash, hex and width_bucket didn't finish translating. An argument
that isn't a column or literal is now read once, through a lambda, so rand() in it is one sample, as in
Spark; a column or literal is still written out as it is."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import math
import zlib
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Callable
from urllib.parse import quote_plus

import pytest

from fabric_desktop import sql
from fabric_desktop.spark_builtins_sql import ONCE

LEVELS = 6
SRC = "(SELECT * FROM VALUES (5, 'a,b;c d', 1.25D, DATE'2024-02-28') AS t(i, s, d, dt))"


def _nested(template: str, leaf: str, levels: int = LEVELS) -> str:
    expr = leaf
    for _ in range(levels):
        expr = template.format(expr)
    return expr


def _translated(expr: str) -> str:
    return sql.duckdb_sql(sql.parse(f"SELECT {expr} AS v FROM {SRC}")[0])


def _value(expr: str) -> Any:
    rows = sql.run(f"SELECT {expr} AS v FROM {SRC}").to_pylist()
    assert len(rows) == 1
    return rows[0]["v"]


def _times(fn: Callable[[Any], Any], start: Any, levels: int = LEVELS) -> Any:
    for _ in range(levels):
        start = fn(start)
    return start


def _mime_base64(text: str) -> str:
    """Spark 3.5's base64: a CR LF after every 76 characters."""
    flat = base64.b64encode(text.encode()).decode()
    return "\r\n".join(flat[k:k + 76] for k in range(0, len(flat), 76))


def _java_url_encode(text: str) -> str:
    return quote_plus(text, safe="*").replace("~", "%7E")


def _bround(x: Decimal, places: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def _width_bucket(v: float, lo: float, hi: float, n: int) -> int:
    if v < lo:
        return 0
    if v >= hi:
        return n + 1
    return math.floor((v - lo) * n / (hi - lo)) + 1


def _shiftleft(v: int, k: int) -> int:
    r = (v << k) & 0xFFFFFFFF
    return r - (1 << 32) if r >= 1 << 31 else r


def _next_monday(d: dt.date) -> dt.date:
    return d + dt.timedelta(days=(7 - d.weekday()) % 7 or 7)


def _substring_index(s: str, delim: str, n: int) -> str:
    parts = s.split(delim)
    return delim.join(parts[:n]) if n > 0 else delim.join(parts[n:])


# (name, the call with {} for the nested argument, the innermost argument, the value six levels give, bound)
CASES: list[tuple[str, str, str, Any, int]] = [
    ("base64", "base64({})", "CAST(i + 1 AS STRING)", _times(_mime_base64, "6"), 10_000),
    ("hex", "hex({})", "CAST(i + 1 AS STRING)", _times(lambda s: s.encode().hex().upper(), "6"), 25_000),
    ("crc32", "CAST(crc32({}) AS STRING)", "CAST(i + 1 AS STRING)",
     _times(lambda s: str(zlib.crc32(s.encode())), "6"), 40_000),
    ("sha2", "sha2({}, 256)", "CAST(i + 1 AS STRING)", _times(lambda s: hashlib.sha256(s.encode()).hexdigest(), "6"),
     7_000),
    ("url_encode", "url_encode({})", "concat(s, ' ~*')", _times(_java_url_encode, "a,b;c d ~*"), 6_000),
    ("expm1", "expm1({})", "d - 1.25", _times(math.expm1, 0.0), 3_000),
    ("log1p", "log1p({})", "d - 1", _times(math.log1p, 0.25), 3_000),
    ("pmod", "pmod({} + 7, 4)", "i * 3", _times(lambda v: (v + 7) % 4, 15), 2_500),
    ("pmod divisor", "pmod(100, {} + 3)", "i - 1", _times(lambda v: 100 % (v + 3), 4), 2_500),
    ("bround", "bround({} * 3, 1)", "CAST(d AS DECIMAL(10, 2))",
     _times(lambda v: _bround(v * 3, 1), Decimal("1.25")), 2_500),
    ("width_bucket", "width_bucket({} + 0.5, 0, 10, 5)", "d * 2",
     _times(lambda v: _width_bucket(v + 0.5, 0, 10, 5), 2.5), 6_000),
    ("shiftleft", "shiftleft({} + 1, 3)", "i * 1", _times(lambda v: _shiftleft(v + 1, 3), 5), 65_000),
    ("shiftrightunsigned", "shiftrightunsigned({} - 1, 1)", "i - 100",
     _times(lambda v: ((v - 1) & 0xFFFFFFFF) >> 1, -95), 4_000),
    ("substring_index", "substring_index(concat({}, ',x;y'), ',', 2)", "upper(s)",
     _times(lambda v: _substring_index(v + ",x;y", ",", 2), "A,B;C D"), 40_000),
    ("next_day", "next_day(date_add({}, 1), 'MON')", "dt + 0",
     _times(lambda d: _next_monday(d + dt.timedelta(days=1)), dt.date(2024, 2, 28)), 17_000),
    ("to_date", "to_date(concat(CAST({} AS STRING), ' 10:00'))", "dt + 1", dt.date(2024, 2, 29), 35_000),
    ("date_format", "date_format(date_add({}, 1), 'yyyy-MM-dd')", "dt + 0", "2024-03-05", 9_000),
    ("array_append", "array_append(concat({}, array(1)), i)", "array(i + 1)", [6] + [1, 5] * LEVELS, 1_500),
    ("array_insert", "array_insert(concat({}, array(0)), 1, i)", "array(i - 1)",
     _times(lambda a: [5] + a + [0], [4]), 8_000),  # about 1,200 a level: its padding checks (#3050)
    ("slice", "slice(concat({}, array(9, 8)), 2, 3)", "array(i, i + 1)",
     _times(lambda a: (a + [9, 8])[1:4], [5, 6]), 1_200),
    ("array_distinct", "array_distinct(concat({}, array(i)))", "array(i + 1, i + 1)", [6, 5], 3_000),  # its double check (#3050)
    ("zip_with", "zip_with(concat({}, array(1)), array(1, 2), (x, y) -> coalesce(x, 0) + coalesce(y, 0))",
     "array(i - 5)", _times(lambda a: [(x or 0) + (y or 0) for x, y in zip(a + [1], ([1, 2] + [None] * len(a))[:len(a) + 1], strict=True)], [0]),
     4_500),
    ("nanvl", "nanvl(CAST('NaN' AS DOUBLE) * {}, d + 1)", "d - 1.25", 2.25, 2_000),
    ("split_part", "split_part(concat({}, ';q'), ';', 2)", "upper(s)", "q", 70_000),
    ("hash", "hash({} + 1)", "i * 2", None, 120_000),
    ("xxhash64", "xxhash64({} + 1)", "i * 2", None, 170_000),
]


@pytest.mark.parametrize(("name", "template", "leaf", "want", "bound"), CASES, ids=[c[0] for c in CASES])
def test_six_levels_of_one_function_stay_small(name: str, template: str, leaf: str, want: Any, bound: int) -> None:
    expr = _nested(template, leaf)
    assert len(_translated(expr)) < bound
    got = _value(expr)
    if want is None:  # each level as one call over the level before's value, written out
        value, kind = _value(leaf), _value(f"typeof({leaf})")
        for _ in range(LEVELS):
            call = template.format(f"CAST({value} AS {kind})")
            value, kind = _value(call), _value(f"typeof({call})")
        want = value
    if isinstance(want, float):
        assert got == pytest.approx(want, rel=1e-12)
    else:
        assert got == want


@pytest.mark.parametrize(("template", "leaf"), [
    ("base64({})", "CAST(i + 1 AS STRING)"), ("pmod({} + 7, 4)", "i * 3"), ("width_bucket({} + 0.5, 0, 10, 5)", "d * 2"),
])
def test_each_level_adds_about_the_same(template: str, leaf: str) -> None:
    # A level adds its own SQL, not a copy of the levels inside it.
    sizes = [len(_translated(_nested(template, leaf, k))) for k in (4, 5, 6)]
    assert sizes[2] - sizes[1] < 1.2 * (sizes[1] - sizes[0]) + 50
    assert sizes[2] < 1.5 * sizes[1]


def test_a_column_is_still_written_out_as_it_is() -> None:
    # The common case costs nothing: a column (or literal) argument isn't bound through a lambda.
    for expr in ("base64(s)", "hex(s)", "pmod(i, 3)", "width_bucket(d, 0, 10, 5)", "substring_index(s, ',', 1)",
                 "next_day(dt, 'MON')", "months_between(dt, dt)", "regexp_instr(s, 'b')"):
        assert ONCE not in _translated(expr), expr


def test_a_changing_argument_is_one_sample() -> None:
    # Spark reads each argument once; written out several times, each read was a sample of its own.
    rows = sql.run("SELECT array_repeat(rand(), 3) AS a, pmod(rand() * 10 - 5, 3) AS p, "
                   "dayofweek(next_day(date_add(DATE'2024-01-01', CAST(rand() * 30 AS INT)), 'MON')) AS w, "
                   "width_bucket(rand(), 0, 1, 4) AS b FROM range(500)").to_pylist()
    for row in rows:
        assert len(set(row["a"])) == 1
        assert 0 <= row["p"] < 3
        assert row["w"] == 2
        assert 1 <= row["b"] <= 4


def test_decimal_types_are_kept_through_a_value_read_once() -> None:
    # spark_decimals types pmod and bround from their operands, which now sit in the value read once.
    rows = sql.run("SELECT typeof(pmod(CAST(7.5 AS DECIMAL(4, 2)) + 1, 2)) AS p, "
                   "typeof(pmod(pmod(CAST(7.5 AS DECIMAL(4, 2)) + 1, 2) + 1, 3)) AS q, "
                   "typeof(pmod(7, CAST(2 AS DECIMAL(3, 1)) + 1)) AS r, "
                   "typeof(bround(CAST(2.345 AS DECIMAL(7, 3)) * 1, 2)) AS b").to_pylist()
    assert rows == [{"p": "decimal(5,2)", "q": "decimal(6,2)", "r": "decimal(2,1)", "b": "decimal(9,2)"}]


def test_a_column_named_like_the_lambda_is_still_the_column() -> None:
    # The lambda that reads a value once is named so no column is: a column kf_once (or one named like the
    # lambda's prefix) beside an argument that is bound is still the column, not the lambda's value.
    src = "(SELECT * FROM VALUES (4, 10, 7, DATE'2024-03-31') AS t(kf_once, __kf, a0, kf_t))"
    rows = sql.run(f"SELECT pmod(kf_once + 1, kf_once) AS p, pmod(a0 * 3, __kf - kf_once) AS q, "
                   f"months_between(kf_t, kf_t - kf_once) AS m, base64(base64(CAST(a0 + __kf AS STRING))) AS b, "
                   f"zip_with(array(__kf + 1), array(__kf), (x, y) -> x * y) AS z FROM {src}").to_pylist()
    assert rows == [{"p": 1, "q": 3, "m": pytest.approx(0.12903226), "b": _times(_mime_base64, "17", 2), "z": [110]}]

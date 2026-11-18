"""Snowflake functions at the edges their reference pages name, on seeded inputs (SFX-026).

The reference is a small Python model of each rule as https://docs.snowflake.com/en/sql-reference states it,
with the default session parameters (WEEK_START 0, DATE_INPUT_FORMAT AUTO). Each case runs through
snowflake_sql.translate and DuckDB, as a Snowflake cell does here. Every case must match: these families had
answers that differed (REGEXP_SUBSTR's '' for no match, TIME_SLICE counted from 2000, CEIL and FLOOR through
FLOAT, EDITDISTANCE's negative maximum) or stopped (NEXT_DAY spellings, CONTAINS of NULL, TO_DATE's AUTO
formats and epoch strings).
"""

from __future__ import annotations

import datetime as dt
import random
import re
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf

SEED = 20261118
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
EPOCH, WEEK0, EPOCH_TS = dt.date(1970, 1, 1), dt.date(1969, 12, 29), dt.datetime(1970, 1, 1)


def q(s: str | None) -> str:
    return "NULL" if s is None else "'" + s.replace("'", "''") + "'"


def dq(d: dt.date) -> str:
    return f"'{d.isoformat()}'::DATE"


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _scaled(x: Decimal, scale: int, mode: str) -> Decimal:
    step = Decimal(1).scaleb(-scale)
    return (x / step).quantize(Decimal(1), rounding=mode) * step


def _month_slice(d: dt.date, months: int) -> tuple[dt.date, dt.date]:
    first = ((d.year - 1970) * 12 + d.month - 1) // months * months
    after = first + months
    return dt.date(1970 + first // 12, first % 12 + 1, 1), dt.date(1970 + after // 12, after % 12 + 1, 1)


def cases() -> list[tuple[str, str, object]]:
    r = random.Random(SEED)
    out: list[tuple[str, str, object]] = []
    # regexp_substr: "returns NULL in the following cases: No match is found."
    texts, patterns = ["abc123def456", "aaa", "Hello World", "", "x1y22z333", "no digits"], ["[0-9]+", "a", "o", "z",
                                                                                              "[A-Z]", "l+"]
    for _ in range(40):
        s, p = r.choice(texts), r.choice(patterns)
        pos, occ = r.randint(1, max(1, len(s))), r.randint(1, 3)
        found = list(re.finditer(p, s[pos - 1:]))
        out.append(("REGEXP_SUBSTR", f"REGEXP_SUBSTR({q(s)}, {q(p)}, {pos}, {occ})",
                    found[occ - 1].group(0) if len(found) >= occ else None))
        first = re.search(p, s)
        out.append(("REGEXP_SUBSTR", f"REGEXP_SUBSTR({q(s)}, {q(p)})", first.group(0) if first else None))
    out += [("REGEXP_SUBSTR", "REGEXP_SUBSTR('abc', 'z') || 'x'", None),
            ("REGEXP_SUBSTR", "COALESCE(REGEXP_SUBSTR('abc', 'z'), 'none')", "none"),
            ("REGEXP_SUBSTR", "REGEXP_SUBSTR('ABC', 'b', 1, 1, 'i')", "B"),
            ("REGEXP_SUBSTR", "REGEXP_SUBSTR('ABC', 'z', 1, 1, 'i')", None),
            ("REGEXP_SUBSTR", "REGEXP_SUBSTR('abc', 'x*')", ""),  # an empty match is a match
            # regexp_substr parameters: "s ... the POSIX wildcard character . matches \n", "m ... multi-line mode"
            ("REGEXP_SUBSTR", "REGEXP_SUBSTR('a' || CHR(10) || 'b', 'a.b', 1, 1, 's')", "a\nb"),
            ("REGEXP_SUBSTR", "REGEXP_SUBSTR('A' || CHR(10) || 'b', 'a.b', 1, 1, 'is')", "A\nb"),
            ("REGEXP_SUBSTR", "REGEXP_SUBSTR('a' || CHR(10) || 'b', 'a.b')", None),
            ("REGEXP_SUBSTR", "REGEXP_SUBSTR('x' || CHR(10) || 'ab', '^a', 1, 1, 'm')", "a"),
            ("REGEXP_SUBSTR", "REGEXP_SUBSTR('a' || CHR(10) || 'ab', '^a', 1, 2, 'm')", "a"),
            ("REGEXP_SUBSTR", "REGEXP_SUBSTR('x' || CHR(10) || 'Ab', '^a', 1, 1, 'mi')", "A")]
    # contains: "Returns NULL if either input expression is NULL."
    for a, b in [("abc", "b"), ("abc", "z"), (None, "a"), ("abc", None), (None, None)]:
        out.append(("CONTAINS", f"CONTAINS({q(a)}, {q(b)})", None if a is None or b is None else b in a))
    # next_day: "The string must start with the first two characters (case-insensitive) of the day name" and "Any
    # leading spaces and trailing characters, including spaces, in the string are ignored."
    for _ in range(30):
        d, wd = dt.date(2024, 1, 1) + dt.timedelta(days=r.randint(0, 400)), r.randrange(7)
        name = DAYS[wd]
        spelled = r.choice([name[:2], name[:3], name, name.upper(), name.capitalize(), "  " + name[:2], name + " ",
                            name[:2] + "xyz", " " + name.upper() + "  "])
        out.append(("NEXT_DAY", f"NEXT_DAY({dq(d)}, {q(spelled)})", d + dt.timedelta(days=(wd - d.weekday() - 1) % 7 + 1)))
        out.append(("PREVIOUS_DAY", f"PREVIOUS_DAY({dq(d)}, {q(spelled)})",
                    d - dt.timedelta(days=(d.weekday() - wd - 1) % 7 + 1)))
    # time_slice: "All slices are aligned relative to midnight January 1, 1970 (1970-01-01 00:00:00)"; for WEEK "the
    # starts of the slices are aligned with the beginning of the week that contains January 1, 1970."
    for _ in range(40):
        d, n = dt.date(1965, 1, 1) + dt.timedelta(days=r.randint(0, 25000)), r.randint(1, 7)
        unit = r.choice(["DAY", "WEEK", "MONTH", "QUARTER", "YEAR"])
        if unit in ("DAY", "WEEK"):
            origin, days = (EPOCH, n) if unit == "DAY" else (WEEK0, 7 * n)
            start = origin + dt.timedelta(days=(d - origin).days // days * days)
            end = start + dt.timedelta(days=days)
        else:
            start, end = _month_slice(d, {"MONTH": 1, "QUARTER": 3, "YEAR": 12}[unit] * n)
        out.append(("TIME_SLICE", f"TIME_SLICE({dq(d)}, {n}, '{unit}')", start))
        out.append(("TIME_SLICE", f"TIME_SLICE({dq(d)}, {n}, '{unit}', 'END')", end))
    for _ in range(40):
        ts = EPOCH_TS + dt.timedelta(seconds=r.randint(-100_000_000, 1_900_000_000))
        unit, seconds = r.choice([("HOUR", 3600), ("MINUTE", 60), ("SECOND", 1)])
        width = r.randint(1, 9) * seconds
        start = EPOCH_TS + dt.timedelta(seconds=int((ts - EPOCH_TS).total_seconds()) // width * width)
        out.append(("TIME_SLICE", f"TIME_SLICE('{ts.isoformat(sep=' ')}'::TIMESTAMP_NTZ, {width // seconds}, '{unit}')",
                    start))
    # date-time-input-output: the AUTO date formats are YYYY-MM-DD, DD-MON-YYYY and MM/DD/YYYY. to_date: "If the format
    # of the input parameter is a string that contains an integer ... the integer is treated as a number of seconds,
    # milliseconds, microseconds, or nanoseconds" (31536000 is 1971-01-01).
    for _ in range(15):
        d = dt.date(1990, 1, 1) + dt.timedelta(days=r.randint(0, 15000))
        for text in (d.isoformat(), d.strftime("%d-%b-%Y").upper(), d.strftime("%d-%b-%Y"), d.strftime("%m/%d/%Y")):
            out.append(("TO_DATE", f"TO_DATE({q(text)})", d))
            out.append(("TO_DATE", f"{q(text)}::DATE", d))
        seconds = (d - EPOCH).days * 86400 + r.randint(0, 86399)
        out.append(("TO_DATE", f"TO_DATE({q(str(seconds))})", d))
        out.append(("TO_DATE", f"TO_DATE({q(str(seconds * 1000))})", d))
    out += [("TO_DATE", "TO_DATE('31536000')", dt.date(1971, 1, 1)), ("TO_DATE", "TRY_TO_DATE('31-FEB-2025')", None),
            ("TO_DATE", "TRY_TO_DATE('02/30/2025')", None)]
    # editdistance: "If you specify a negative number (that is, -n), the function uses 0 as the maximum distance and
    # returns 0."
    for _ in range(25):
        a, b, most = r.choice(["kitten", "", "abc", "flaw"]), r.choice(["sitting", "abc", "lawn", ""]), r.randint(-3, 5)
        out.append(("EDITDISTANCE", f"EDITDISTANCE({q(a)}, {q(b)}, {most})", 0 if most < 0 else min(_lev(a, b), most)))
    out.append(("EDITDISTANCE", "EDITDISTANCE('abc', 'abd', NULL)", None))
    # ceil and floor: "If scale_expr is larger than the input expression scale, the function does not have any effect",
    # a negative scale counts places before the point, and "If the input expression is a NUMBER, the returned type is a
    # NUMBER" (worked out exactly). trunc: toward 0.
    for _ in range(60):
        x, scale = Decimal(r.randint(-99999, 99999)).scaleb(-r.randint(0, 3)), r.randint(-2, 3)
        for name, mode in (("CEIL", ROUND_CEILING), ("FLOOR", ROUND_FLOOR), ("TRUNC", ROUND_DOWN)):
            out.append((name, f"{name}({x}, {scale})", _scaled(x, scale, mode)))
    for name, x in (("FLOOR", "0.29"), ("FLOOR", "1.15"), ("CEIL", "0.07"), ("CEIL", "17.94"), ("FLOOR", "4.35")):
        out.append((name, f"{name}({x}, 2)", Decimal(x)))
    return out


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def _same(want: object, got: object) -> bool:
    if isinstance(want, Decimal):
        return isinstance(got, (int, Decimal)) and not isinstance(got, bool) and Decimal(got) == want
    return type(want) is type(got) and want == got


def test_snowflake_functions_follow_their_documented_edges(con: duckdb.DuckDBPyConnection) -> None:
    wrong = []
    for family, expression, want in cases():
        translation = sf.translate("SELECT " + expression)
        if not translation.runnable:
            got: object = f"refused: {translation.first_refusal()}"
        else:
            try:
                got = con.execute(translation.duckdb_sql).fetchall()[0][0]
            except duckdb.Error as exc:
                got = f"error: {str(exc).splitlines()[0]}"
        if not _same(want, got):
            wrong.append(f"{family}: SELECT {expression}  Snowflake: {want!r}  here: {got!r}")
    assert not wrong, f"{len(wrong)} answers differ from Snowflake's rules:\n" + "\n".join(wrong)


def test_cases_are_seeded() -> None:
    first = cases()
    assert first == cases() and len(first) > 600


@pytest.mark.parametrize("day", ["xx", "", "  ", "1monday"])
def test_a_day_that_names_no_weekday_is_refused_by_name(day: str) -> None:
    translation = sf.translate(f"SELECT NEXT_DAY('2024-01-05'::DATE, {q(day)})")
    assert not translation.runnable and "first two letters" in str(translation.first_refusal())


def test_a_ceil_scale_that_is_not_written_out_keeps_working(con: duckdb.DuckDBPyConnection) -> None:
    translation = sf.translate("SELECT CEIL(x, s) FROM (VALUES (17.941, 2)) AS t(x, s)")
    assert translation.runnable
    assert float(con.execute(translation.duckdb_sql).fetchall()[0][0]) == pytest.approx(17.95)


def test_a_scale_past_38_digits_builds_no_huge_text() -> None:
    translation = sf.translate("SELECT CEIL(1.5, 999999999), FLOOR(1.5, -999999999)")
    assert translation.runnable and len(translation.duckdb_sql) < 500


def test_a_scale_the_exact_path_does_not_take_still_answers(con: duckdb.DuckDBPyConnection) -> None:
    # 37 and 38 places, and a NUMBER(38, 2) near 10^35 at its own scale: no DECIMAL past 38 digits, no overflow.
    expressions = ["CEIL(17.941::NUMBER(18,3), 38)", "FLOOR(17.941::NUMBER(18,3), 37)",
                   "CEIL(123456789012345678901234567890123456.78::NUMBER(38,2), 2)",
                   "FLOOR(123456789012345678901234567890123456.78::NUMBER(38,2), 1)"]
    translation = sf.translate("SELECT " + ", ".join(expressions))
    got = con.execute(translation.duckdb_sql).fetchall()[0]
    assert float(got[0]) == pytest.approx(17.941) and float(got[1]) == pytest.approx(17.941)
    assert got[2] == Decimal("123456789012345678901234567890123456.78")
    assert got[3] == Decimal("123456789012345678901234567890123456.7")

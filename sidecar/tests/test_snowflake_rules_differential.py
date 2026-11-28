"""Differential test of local Snowflake SQL against Snowflake's documented rules, on generated inputs.

No Snowflake account is used. The reference is a small Python model of each rule as Snowflake's SQL reference
states it (quoted beside each family, from https://docs.snowflake.com/en/sql-reference/functions/<name>), with
the default session parameters (WEEK_START 0, DEFAULT_NULL_ORDERING LAST). Inputs a page doesn't cover, such as
SUBSTR's start past the end, aren't drawn. A seeded generator draws the inputs and each case runs through
snowflake_sql.translate and DuckDB, as a Snowflake cell does here.

An answer that differs, or an error, must be named in KNOWN until it is fixed or refused by name. A KNOWN entry
that no case needs any more fails too.
"""

from __future__ import annotations

import calendar
import datetime as dt
import random
import re
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

import duckdb

from fabric_desktop import snowflake_sql as sf

SEED = 20261117
WORDS = ["", "a", "abc", "mystring", "Hello World", "  pad  ", "11.22.33", "a,b,,c", "é", "banana"]

# Families that still differ, and why. Delete an entry once it is fixed.
KNOWN: dict[str, str] = {}


def q(s: str | None) -> str:
    return "NULL" if s is None else "'" + s.replace("'", "''") + "'"


def _substr(s: str, start: int, length: int) -> str:
    # substr: "If 0 is specified, it is treated as 1." "If a negative value is specified, the starting position
    # is computed as the start_expr characters ... from the end". "If the length is a negative number, the
    # function returns an empty string."
    if length < 0:
        return ""
    if start == 0:
        start = 1
    if start < 0:
        start = len(s) + start + 1
    return s[start - 1:start - 1 + length]


def _tdiv(a: int, b: int) -> int:
    quotient = abs(a) // abs(b)
    return quotient if (a < 0) == (b < 0) else -quotient


def _round(x: Decimal, places: int, mode: str) -> Decimal:
    # round: "By default, half-points are rounded away from zero for decimals"; 'HALF_TO_EVEN' rounds half to
    # even; "If scale_expr is negative, it specifies the number of places before the decimal point".
    step = Decimal(1).scaleb(-places)
    return x.quantize(step, rounding=mode) if places >= 0 else (x / step).quantize(Decimal(1), rounding=mode) * step


def _months_between(a: dt.datetime, b: dt.datetime) -> Decimal:
    # months_between: the fraction uses "the day and time parts of the input values", each month 31 days long; it is
    # zero "If the days of the month are the same" or both are the last day of a month.
    months = (a.year * 12 + a.month) - (b.year * 12 + b.month)
    last = lambda d: d.day == calendar.monthrange(d.year, d.month)[1]  # noqa: E731
    if a.day == b.day or (last(a) and last(b)):
        return Decimal(months)
    seconds = lambda d: d.hour * 3600 + d.minute * 60 + d.second  # noqa: E731
    return Decimal(months) + Decimal((a.day - b.day) * 86400 + seconds(a) - seconds(b)) / Decimal(2678400)


def _boundaries(unit: str, a: dt.date, b: dt.date) -> int:
    # datediff counts boundaries; for week, WEEK_START 0 is "Calculated using weeks starting on Monday."
    monday = lambda d: d - dt.timedelta(days=d.weekday())  # noqa: E731
    return {"year": lambda: b.year - a.year,
            "quarter": lambda: (b.year * 4 + (b.month - 1) // 3) - (a.year * 4 + (a.month - 1) // 3),
            "month": lambda: (b.year * 12 + b.month) - (a.year * 12 + a.month),
            "day": lambda: (b - a).days,
            "week": lambda: (monday(b) - monday(a)).days // 7}[unit]()


def _position(find: str, text: str, start: int) -> int | None:
    # position: "If the specified optional start_pos is beyond the end of the second argument ... returns 0."
    # "If the first argument is empty ... the function returns 1." Not found: 0.
    if start > len(text):
        return 0
    if find == "":
        return 1 if start == 1 else None  # an empty search from a later start isn't documented
    i = text.find(find, start - 1)
    return i + 1 if i >= 0 else 0


def _split_part(s: str, sep: str, part: int) -> str:
    # split_part: "If the partNumber is 0, it is treated as 1." Negative counts from the end; out of range is
    # an empty string; "If the delimiter is an empty string ... the returned value is the input string."
    parts = s.split(sep) if sep else [s]
    part = 1 if part == 0 else (len(parts) + part + 1 if part < 0 else part)
    return parts[part - 1] if 1 <= part <= len(parts) else ""


def _pad(s: str, n: int, pad: str, left: bool) -> str:
    # lpad: "If the base argument is longer than length_expr, it is truncated to length length_expr." "If the
    # pad argument is empty, no padding is inserted, but the result is still truncated".
    if len(s) >= n or not pad:
        return s[:max(n, 0)]  # a negative length gives ''
    fill = (pad * n)[:n - len(s)]
    return fill + s if left else s + fill


def _regex(pattern: str, params: str) -> re.Pattern[str]:
    # functions-regexp: "m Enables multi-line mode" (^ and $ at each line; without it they "mark the beginning
    # and end of the entire subject"), "s Enables the POSIX wildcard character . to match \n", and of c and i
    # "the one that occurs last in the string dictates" the case. Python's $ also matches before a final line
    # break, so without m it is written \Z here.
    flags = (re.M if "m" in params else 0) | (re.S if "s" in params else 0)
    case = [c for c in params if c in "ci"]
    flags |= re.I if case and case[-1] == "i" else 0
    return re.compile(pattern if "m" in params else pattern.replace("$", r"\Z"), flags)


def _regex_cases(r: random.Random) -> list[tuple[str, str, object]]:
    """WR-257: the regular expression parameters, and the empty pattern ("In most regexp functions, an empty
    pattern (that is, '') matches nothing, not even an empty subject"; in REGEXP_LIKE "'' automatically becomes
    '^$'")."""
    out: list[tuple[str, str, object]] = []
    subjects = ["a\nb", "ab\nab", "x\na", "", "A\nb", "abc", "b\na\nb"]
    for _ in range(120):
        s, params = r.choice(subjects), r.choice(["", "m", "s", "ms", "sm", "i", "mi", "c", "im", "ic", "ci", "smi"])
        p = r.choice(["^b", "a$", "^a.*b$", "a.b", "b$", "^a", "a.", "", "^A", "(a|b)$"])
        rx = _regex(p, params)
        out.append(("REGEXP_LIKE_PARAMS", f"REGEXP_LIKE({q(s)}, {q(p)}, {q(params)})",
                    s == "" if p == "" else rx.fullmatch(s) is not None))
        found = [] if p == "" else [m.group(0) for m in rx.finditer(s)]
        out.append(("REGEXP_SUBSTR_PARAMS", f"REGEXP_SUBSTR({q(s)}, {q(p)}, 1, 1, {q(params)})",
                    found[0] if found else None))
        out.append(("REGEXP_SUBSTR_ALL_PARAMS", f"REGEXP_SUBSTR_ALL({q(s)}, {q(p)}, 1, 1, {q(params)})", found))
        out.append(("REGEXP_COUNT_PARAMS", f"REGEXP_COUNT({q(s)}, {q(p)}, 1, {q(params)})", len(found)))
        out.append(("REGEXP_REPLACE_PARAMS", f"REGEXP_REPLACE({q(s)}, {q(p)}, 'X', 1, 0, {q(params)})",
                    s if p == "" else rx.sub("X", s)))
    for s in ["abc", "", "a\nb"]:
        out.append(("REGEXP_REPLACE_PARAMS", f"REGEXP_REPLACE({q(s)}, '', '-')", s))
        out.append(("REGEXP_REPLACE_PARAMS", f"REGEXP_REPLACE(v, p, '-') FROM (SELECT {q(s)} AS v, '' AS p)", s))
        out.append(("REGEXP_SUBSTR_PARAMS", f"REGEXP_SUBSTR(v, p) FROM (SELECT {q(s)} AS v, '' AS p)", None))
        out.append(("REGEXP_SUBSTR_ALL_PARAMS", f"REGEXP_SUBSTR_ALL({q(s)}, '')", []))
    return out


def cases() -> list[tuple[str, str, object]]:
    r = random.Random(SEED)
    out: list[tuple[str, str, object]] = []
    for _ in range(150):
        s = r.choice(WORDS)
        start, length = (r.randint(-len(s), len(s)) if s else 0), r.randint(-2, 9)
        out.append(("SUBSTR", f"SUBSTR({q(s)}, {start}, {length})", _substr(s, start, length)))
    for _ in range(60):
        s, n = r.choice(WORDS), r.randint(-2, 12)
        # left/right: "If the length is a negative number, the function returns an empty string."
        out.append(("LEFT", f"LEFT({q(s)}, {n})", "" if n < 0 else s[:n]))
        out.append(("RIGHT", f"RIGHT({q(s)}, {n})", "" if n <= 0 else s[-n:]))
        m = r.randint(0, 3)  # repeat: "The minimum valid number is 0 (which results in an empty string)."
        out.append(("REPEAT", f"REPEAT({q(s)}, {m})", s * m))
    for _ in range(60):
        args = [r.choice(["a", "b", None, ""]) for _ in range(r.randint(2, 4))]
        listed = ", ".join(map(q, args))
        joined = None if None in args else "".join(x or "" for x in args)
        # concat: "If any input value is NULL, the function returns NULL." concat_ws: "If any argument is NULL,
        # the function returns NULL." ... "doesn't skip NULL values."
        out.append(("CONCAT", f"CONCAT({listed})", joined))
        out.append(("PIPES", " || ".join(map(q, args)), joined))
        out.append(("CONCAT_WS", f"CONCAT_WS(',', {listed})",
                    None if None in args else ",".join(x or "" for x in args)))
    for _ in range(100):
        a, b = r.randint(-30, 30), r.choice([x for x in range(-9, 10) if x])
        # mod: "the remainder from truncation-based division"; "if expr1 is negative, the returned value is
        # negative."
        out.append(("MOD", f"MOD({a}, {b})", a - b * _tdiv(a, b)))
        z = r.choice([0, b, None])
        # div0: "If the divisor is 0, the function returns 0." div0null: "If the divisor is 0 or NULL ... 0."
        out.append(("DIV0", f"DIV0({a}, {z or 0})", Decimal(a) / Decimal(z) if z else Decimal(0)))
        out.append(("DIV0NULL", f"DIV0NULL({a}, {q(None) if z is None else z})",
                    Decimal(a) / Decimal(z) if z else Decimal(0)))
        x, places = Decimal(r.randint(-99999, 99999)) / 1000, r.randint(-2, 2)
        out.append(("ROUND", f"ROUND({x}::NUMBER(10,3), {places})", _round(x, places, ROUND_HALF_UP)))
        out.append(("ROUND_HALF_TO_EVEN", f"ROUND({x}::NUMBER(10,3), {places}, 'HALF_TO_EVEN')",
                    _round(x, places, ROUND_HALF_EVEN)))
    for _ in range(100):
        a = dt.date(2023, 12, 25) + dt.timedelta(days=r.randint(-500, 500))
        b = dt.date(2023, 12, 25) + dt.timedelta(days=r.randint(-500, 500))
        unit = r.choice(["year", "quarter", "month", "day", "week"])
        out.append(("DATEDIFF", f"DATEDIFF({unit}, '{a}'::DATE, '{b}'::DATE)", _boundaries(unit, a, b)))
        # functions-date-time: "With WEEK_START set to 0, the DOW for Sunday is 0"; DAYOFWEEKISO 1 to 7.
        out.append(("DAYOFWEEK", f"DAYOFWEEK('{a}'::DATE)", (a.weekday() + 1) % 7))
        out.append(("DAYOFWEEKISO", f"DAYOFWEEKISO('{a}'::DATE)", a.weekday() + 1))
        # date_trunc: "Truncates the input week to start on Monday."
        out.append(("DATE_TRUNC_WEEK", f"DATE_TRUNC('week', '{a}'::DATE)", a - dt.timedelta(days=a.weekday())))
        out.append(("WEEKISO", f"WEEKISO('{a}'::DATE)", a.isocalendar()[1]))
        out.append(("LAST_DAY", f"LAST_DAY('{a}'::DATE)", dt.date(a.year, a.month, calendar.monthrange(a.year, a.month)[1])))
    for _ in range(120):
        text, find, start = r.choice(WORDS), r.choice(["a", "an", "b", "", " ", "z", "A", "ing"]), r.randint(1, 12)
        want = _position(find, text, start)
        if want is not None:
            out.append(("POSITION", f"POSITION({q(find)}, {q(text)}, {start})", want))
            out.append(("CHARINDEX", f"CHARINDEX({q(find)}, {q(text)}, {start})", want))
        s, sep, part = r.choice(WORDS), r.choice([".", ",", " ", "a", "--", ""]), r.randint(-4, 4)
        out.append(("SPLIT_PART", f"SPLIT_PART({q(s)}, {q(sep)}, {part})", _split_part(s, sep, part)))
    for _ in range(80):
        s, n, pad = r.choice(WORDS), r.randint(-2, 12), r.choice(["0", "*_", "", " ", "ab"])
        out.append(("LPAD", f"LPAD({q(s)}, {n}, {q(pad)})", _pad(s, n, pad, True)))
        out.append(("RPAD", f"RPAD({q(s)}, {n}, {q(pad)})", _pad(s, n, pad, False)))
        # Nested: the inner call answers by the same rules as the outer one.
        inner, outer, pad2 = r.randint(-2, 8), r.randint(-2, 12), r.choice(["", "-"])
        out.append(("LPAD", f"LPAD(LPAD({q(s)}, {inner}, {q(pad)}), {outer}, {q(pad2)})",
                    _pad(_pad(s, inner, pad, True), outer, pad2, True)))
        out.append(("RPAD", f"RPAD(RPAD({q(s)}, {inner}, {q(pad)}), {outer}, {q(pad2)})",
                    _pad(_pad(s, inner, pad, False), outer, pad2, False)))
    nested = random.Random(SEED + 1)  # its own draws, so the cases above keep their inputs
    for _ in range(40):
        # A POSITION in the start of another: the inner one is checked past the end too.
        text, find, word = nested.choice(WORDS), nested.choice(["a", "", "b", "ing"]), nested.choice(WORDS)
        start = nested.randint(1, 12)
        inner = _position("", word, start)  # 0 past the end, 1 from the start
        want = None if inner is None else _position(find, text, inner + 1)
        if want is not None:
            out.append(("POSITION", f"POSITION({q(find)}, {q(text)}, POSITION('', {q(word)}, {start}) + 1)", want))
    stamps = random.Random(SEED + 2)  # WR-258: timestamps and times written as text
    for _ in range(80):
        t = dt.datetime(1900, 1, 1) + dt.timedelta(microseconds=stamps.randint(0, 200 * 365 * 86400 * 10 ** 6))
        text = t.isoformat(sep=" ")
        # parameters: TIMESTAMP_NTZ_OUTPUT_FORMAT defaults to "YYYY-MM-DD HH24:MI:SS.FF3" and TIME_OUTPUT_FORMAT
        # to "HH24:MI:SS"; TO_VARCHAR with no format, a cast to text and || use them.
        want = f"{t:%Y-%m-%d %H:%M:%S}.{t.microsecond // 1000:03d}"
        out.append(("TIMESTAMP_TEXT", f"{q(text)}::TIMESTAMP_NTZ::VARCHAR", want))
        out.append(("TIMESTAMP_TEXT", f"TO_VARCHAR({q(text)}::TIMESTAMP_NTZ)", want))
        out.append(("TIMESTAMP_TEXT", f"'at ' || {q(text)}::TIMESTAMP_NTZ", "at " + want))
        out.append(("TIME_TEXT", f"CAST({q(t.time().isoformat())}::TIME AS VARCHAR)", f"{t:%H:%M:%S}"))
    edges = random.Random(SEED + 3)  # WR-279: whole-number casts, MONTHS_BETWEEN, HALF_TO_EVEN, LISTAGG
    for _ in range(60):
        # data-types-numeric: INT and its synonyms are "Synonymous with NUMBER ... NUMBER(38, 0)", so a FLOAT cast to
        # INT rounds as a cast to NUMBER does, half away from zero
        half = Decimal(edges.randint(-4000, 4000)) / 2
        out.append(("WHOLE_CAST", f"{half}::FLOAT::{edges.choice(['INT', 'INTEGER', 'BIGINT', 'SMALLINT'])}",
                    half.quantize(Decimal(1), rounding=ROUND_HALF_UP)))
        big = edges.randint(2**31, 2**62)
        out.append(("WHOLE_CAST", f"TRY_CAST('{big}' AS INT)", Decimal(big)))
        a = dt.datetime(2019, 1, 1) + dt.timedelta(seconds=edges.randint(0, 3 * 365 * 86400))
        b = dt.datetime(2019, 1, 1) + dt.timedelta(seconds=edges.randint(0, 3 * 365 * 86400))
        if edges.random() < 0.2:
            b = b.replace(day=min(a.day, calendar.monthrange(b.year, b.month)[1]))
        out.append(("MONTHS_BETWEEN", f"MONTHS_BETWEEN('{a}'::TIMESTAMP, '{b}'::TIMESTAMP)", _months_between(a, b)))
        out.append(("MONTHS_BETWEEN", f"MONTHS_BETWEEN('{a.date()}'::DATE, '{b.date()}'::DATE)",
                    _months_between(dt.datetime.combine(a.date(), dt.time()), dt.datetime.combine(b.date(), dt.time()))))
        # round: 'HALF_TO_EVEN' on values exactly half way at the scale
        places = edges.randint(0, 4)
        tie = (Decimal(edges.randint(-99999, 99999)) + Decimal("0.5")).scaleb(-places)
        out.append(("ROUND_HALF_TO_EVEN", f"ROUND({tie}, {places}, 'HALF_TO_EVEN')", _round(tie, places, ROUND_HALF_EVEN)))
        # listagg: "If no delimiter is specified, an empty string is used as the delimiter."
        words = [edges.choice(["a", "bc", None, "d"]) for _ in range(edges.randint(0, 4))]
        rows = " UNION ALL ".join(f"SELECT {i} AS i, {q(w)} AS w" for i, w in enumerate(words)) or "SELECT 0 AS i, 'x' AS w WHERE FALSE"
        out.append(("LISTAGG", f"LISTAGG(w) WITHIN GROUP (ORDER BY i) FROM ({rows})", "".join(w for w in words if w)))
    return out + _regex_cases(random.Random(SEED + 2))


def _run(con: duckdb.DuckDBPyConnection, expression: str) -> object:
    translation = sf.translate("SELECT " + expression)
    if not translation.runnable:
        return f"refused: {translation.first_refusal()}"
    try:
        value = con.execute(translation.duckdb_sql).fetchall()[0][0]
    except duckdb.Error as exc:
        return f"error: {str(exc).splitlines()[0]}"
    return value.date() if isinstance(value, dt.datetime) else value


def _same(want: object, got: object) -> bool:
    if isinstance(want, Decimal):
        return isinstance(got, (int, float, Decimal)) and not isinstance(got, bool) \
            and abs(Decimal(str(got)) - want) < Decimal("1e-6")
    return type(want) is type(got) and want == got


def test_local_snowflake_follows_the_documented_rules() -> None:
    con, unexplained, off = duckdb.connect(), [], set()
    for family, expression, want in cases():
        got = _run(con, expression)
        if _same(want, got):
            continue
        off.add(family)
        if family not in KNOWN:
            unexplained.append(f"{family}: SELECT {expression}  Snowflake: {want!r}  here: {got!r}")
    assert not unexplained, f"{len(unexplained)} answers differ from Snowflake's rules:\n" + "\n".join(unexplained)
    assert not set(KNOWN) - off, f"fixed, so delete from KNOWN: {sorted(set(KNOWN) - off)}"


def test_order_by_puts_nulls_last_ascending_and_first_descending() -> None:
    """order-by: with DEFAULT_NULL_ORDERING LAST (the default) NULLs come last in ASC, first in DESC."""
    r, con = random.Random(SEED), duckdb.connect()
    for _ in range(30):
        values = [r.choice([None, *range(-3, 4)]) for _ in range(r.randint(1, 8))]
        rows = ", ".join(f"({'NULL' if v is None else v})" for v in values)
        for direction, nulls_first in (("ASC", False), ("DESC", True)):
            translation = sf.translate(f"SELECT x FROM (VALUES {rows}) AS t(x) ORDER BY x {direction}")
            got = [row[0] for row in con.execute(translation.duckdb_sql).fetchall()]
            known = sorted((v for v in values if v is not None), reverse=direction == "DESC")
            nulls = [None] * values.count(None)
            assert got == (nulls + known if nulls_first else known + nulls), (values, direction, got)


def test_cases_are_seeded_and_cover_every_known_family() -> None:
    first = cases()
    assert first == cases() and len(first) > 1500
    assert {f for f, _, _ in first} >= set(KNOWN) | {"POSITION", "CHARINDEX", "LPAD", "RPAD"}


def test_a_timestamp_with_a_time_zone_as_text_has_the_offset() -> None:
    """parameters: TIMESTAMP_OUTPUT_FORMAT defaults to "YYYY-MM-DD HH24:MI:SS.FF3 TZHTZM", which TIMESTAMP_LTZ and
    TIMESTAMP_TZ use when their own output formats are unset."""
    con = duckdb.connect()
    for zone, want in (("UTC", "2024-01-02 03:04:05.678 +0000"), ("Asia/Kolkata", "2024-01-02 08:34:05.678 +0530"),
                       ("America/St_Johns", "2024-01-01 23:34:05.678 -0330")):
        con.execute(f"SET TimeZone = '{zone}'")
        translation = sf.translate("SELECT '2024-01-02 03:04:05.678912+00:00'::TIMESTAMP_LTZ::VARCHAR")
        assert con.execute(translation.duckdb_sql).fetchall() == [(want,)], zone


def test_nanoseconds_are_refused_by_name() -> None:
    """data-types-datetime: TIMESTAMP's precision is 9 (nanoseconds) by default; DuckDB keeps microseconds, so
    each of these would lose digits without a word."""
    for code in ("SELECT '2024-01-02 03:04:05.1234567'::TIMESTAMP_NTZ", "SELECT TO_TIME('03:04:05.000000001')",
                 "SELECT TRY_TO_TIMESTAMP('2024-01-02 03:04:05.123456789')",
                 "SELECT DATEADD(NANOSECOND, 1, '2024-01-02'::TIMESTAMP_NTZ)",
                 "SELECT TIMEADD(ns, n, '03:04:05'::TIME) FROM t",
                 "SELECT TIMESTAMP_FROM_PARTS(2024, 1, 2, 3, 4, 5, 123456789)", "SELECT TIME_FROM_PARTS(3, 4, 5, n) FROM t",
                 "SELECT TO_TIMESTAMP(1704164645123456789, 9)", "SELECT TO_TIMESTAMP(n, 7) FROM t"):
        translation = sf.translate(code)
        assert not translation.runnable and "Nanoseconds" in translation.first_refusal(), code
        assert "nothing ran" in translation.first_refusal(), code
    con = duckdb.connect()
    for code, want in (("SELECT '2024-01-02 03:04:05.1234560'::TIMESTAMP_NTZ", dt.datetime(2024, 1, 2, 3, 4, 5, 123456)),
                       ("SELECT DATEADD(NANOSECOND, 3000, '2024-01-02'::TIMESTAMP_NTZ)", dt.datetime(2024, 1, 2, 0, 0, 0, 3)),
                       ("SELECT TIMESTAMP_FROM_PARTS(2024, 1, 2, 3, 4, 5, 123456000)", dt.datetime(2024, 1, 2, 3, 4, 5, 123456)),
                       ("SELECT TO_TIMESTAMP(1704164645123456000, 9)", dt.datetime(2024, 1, 2, 3, 4, 5, 123456))):
        assert con.execute(sf.translate(code).duckdb_sql).fetchall() == [(want,)], code


def test_windows_take_snowflakes_default_frames() -> None:
    """window-functions (window frame usage notes): with ORDER BY and no frame, SUM, COUNT and the other
    aggregates use RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW, so ties are summed together;
    FIRST_VALUE, LAST_VALUE and NTH_VALUE use the whole partition, ROWS BETWEEN UNBOUNDED PRECEDING AND
    UNBOUNDED FOLLOWING. NULLs sort last ascending (DEFAULT_NULL_ORDERING LAST)."""
    con = duckdb.connect()
    sql = ("SELECT k, SUM(v) OVER (ORDER BY k), COUNT(*) OVER (ORDER BY k), LAST_VALUE(v) OVER (ORDER BY k), "
           "NTH_VALUE(v, 2) OVER (ORDER BY k), FIRST_VALUE(v) OVER (ORDER BY k DESC), "
           "SUM(v) OVER (ORDER BY k RANGE BETWEEN 1 PRECEDING AND CURRENT ROW) "
           "FROM (VALUES (1, 10), (2, 20), (2, 30), (NULL, 5), (4, NULL)) AS t(k, v) ORDER BY k, v")
    rows = con.execute(sf.translate(sql).duckdb_sql).fetchall()
    assert rows == [(1, 10, 1, 5, 20, 5, 10), (2, 60, 3, 5, 20, 5, 60), (2, 60, 3, 5, 20, 5, 60),
                    (4, 60, 4, 5, 20, 5, None), (None, 65, 5, 5, 20, 5, 5)]


def test_a_set_operation_of_text_and_numbers_is_refused_by_name() -> None:
    """operators-query: a UNION of a VARCHAR and an INTEGER column converts the text to a number ("Numeric value
    'Smith, Jane' is not recognized"); DuckDB made the column text, so '10' sorted before 9 (SFN-032)."""
    for sql in ["SELECT '10' AS a UNION ALL SELECT 9", "SELECT 1 AS a, 'x' AS b UNION SELECT 2, 3",
                "SELECT 1 AS a INTERSECT SELECT '1'", "SELECT 1 AS a MINUS SELECT '1'",
                "SELECT 1 AS a UNION ALL SELECT NULL UNION ALL SELECT '2'::VARCHAR"]:
        translation = sf.translate(sql)
        assert not translation.runnable and translation.statements[0].notes[0].id == "SFN-032", sql
    for sql in ["SELECT '10' AS a UNION ALL SELECT '9'", "SELECT 1 AS a UNION ALL SELECT NULL",
                "SELECT '10'::NUMBER AS a UNION ALL SELECT 9"]:
        assert sf.translate(sql).runnable, sql

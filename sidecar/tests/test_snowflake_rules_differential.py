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

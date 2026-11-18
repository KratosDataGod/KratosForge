"""Differential test of local T-SQL against T-SQL's documented rules, on generated inputs.

There is no warehouse in CI, so the reference is a small Python model of each rule as Microsoft's T-SQL
reference states it (the URL is on each family). A seeded generator draws inputs, the model gives T-SQL's
answer, and the local engine must give the same one. Each text input is written both as '...' and as N'...',
the Unicode literal most T-SQL is written with.

An answer that differs, or an error, must be named in KNOWN until it is fixed or refused by name. A KNOWN entry
that no case needs any more fails too, so the list says only what still differs.
"""

from __future__ import annotations

import calendar
import datetime as dt
import random
import re
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

import pytest

from fabric_desktop.localdata import LocalData

DOCS = "https://learn.microsoft.com/en-us/sql/t-sql/"
SEED = 20261117
WORDS = ["", "a", "abc", "Hello World", "  pad  ", "ab  ", "aBc", "x_y%z", "é", "abcabc", "B", "b"]

# (family, literal prefix) pairs that still differ, and why. Delete an entry once it is fixed.
KNOWN: dict[tuple[str, str], str] = {}


def _sub(s: str, start: int, length: int) -> str:
    # functions/substring-transact-sql: "If start is less than 1, the returned expression will begin at the
    # first character ... the characters returned is the largest value of either the sum of start + length - 1
    # or 0."
    first, end = max(start, 1), start + length
    return s[first - 1:end - 1] if end > first else ""


def _charindex(find: str, text: str, start: int) -> int:
    # functions/charindex-transact-sql: start "0 or negative ... the search starts at the beginning";
    # 0 when not found. An empty search text gives 0 (docs/sql-compatibility.md, Local T-SQL text).
    if find == "":
        return 0
    i = text.find(find, max(start, 1) - 1)
    return i + 1 if i >= 0 else 0


def _tdiv(a: int, b: int) -> int:
    # language-elements/divide-transact-sql: an integer result is truncated toward zero.
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def _round(x: Decimal, places: int, mode: str) -> Decimal:
    # functions/round-transact-sql: ROUND(748.58, -1) is 750.00; a non-zero third argument truncates.
    step = Decimal(1).scaleb(-places)
    return x.quantize(step, rounding=mode) if places >= 0 else (x / step).quantize(Decimal(1), rounding=mode) * step


def _like(text: str, pattern: str) -> bool:
    # language-elements/like-transact-sql: % _ [a-c] [^a]; the text's trailing spaces don't count with ANSI
    # padding (docs/sql-compatibility.md, Local T-SQL text).
    rx, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if c == "[":
            j = pattern.index("]", i + 1)
            body = pattern[i + 1:j]
            rx += "[^" + re.escape(body[1:]) + "]" if body.startswith("^") else "[" + body + "]"
            i = j
        else:
            rx += {"%": ".*", "_": "."}.get(c, re.escape(c))
        i += 1
    compiled = re.compile(rx, re.S)
    return bool(compiled.fullmatch(text) or compiled.fullmatch(text.rstrip(" ")))


def _week_start(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)  # Sunday, a warehouse's DATEFIRST 7


def _boundaries(unit: str, a: dt.date, b: dt.date) -> int:
    # functions/datediff-transact-sql: "the count ... of the specified datepart boundaries crossed".
    return {"year": lambda: b.year - a.year,
            "quarter": lambda: (b.year * 4 + (b.month - 1) // 3) - (a.year * 4 + (a.month - 1) // 3),
            "month": lambda: (b.year * 12 + b.month) - (a.year * 12 + a.month),
            "day": lambda: (b - a).days,
            "week": lambda: (_week_start(b) - _week_start(a)).days // 7}[unit]()


def _add_months(d: dt.date, n: int) -> dt.date:
    # functions/dateadd-transact-sql: a month step to a shorter month ends on its last day.
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def cases(prefix: str) -> list[tuple[str, str, object]]:
    """(family, T-SQL expression, T-SQL's answer) for seeded inputs; text written as ``prefix``'...'."""
    r = random.Random(SEED)

    def q(s: str | None) -> str:
        return "NULL" if s is None else prefix + "'" + s.replace("'", "''") + "'"

    out: list[tuple[str, str, object]] = []
    for _ in range(80):
        s, start, length = r.choice(WORDS), r.randint(-4, 9), r.randint(0, 8)
        out.append(("SUBSTRING", f"SUBSTRING({q(s)}, {start}, {length})", _sub(s, start, length)))
        n = r.randint(0, 8)
        out.append(("LEFT", f"LEFT({q(s)}, {n})", s[:n]))
        out.append(("RIGHT", f"RIGHT({q(s)}, {n})", s[-n:] if n else ""))
        find, start = r.choice(["a", "b", "c", "", " ", "bc", "A", "abc", "l"]), r.randint(-2, 8)
        out.append(("CHARINDEX", f"CHARINDEX({q(find)}, {q(s)}, {start})", _charindex(find, s, start)))
        n = r.randint(-2, 3)  # functions/replicate-transact-sql: a negative count gives NULL
        out.append(("REPLICATE", f"REPLICATE({q(s)}, {n})", None if n < 0 else s * n))
    for s in WORDS:  # functions/len-transact-sql: trailing spaces aren't counted
        out.append(("LEN", f"LEN({q(s)})", len(s.rstrip(" "))))
    codes = random.Random(SEED + 2)  # its own draws too
    for _ in range(40):
        # functions/ascii-transact-sql and unicode-transact-sql: the code of the first character; '' gives NULL.
        s = codes.choice([w for w in WORDS if w.isascii()])
        out.append(("ASCII", f"ASCII({q(s)})", ord(s[0]) if s else None))
        s = codes.choice(WORDS)
        out.append(("UNICODE", f"UNICODE({q(s)})", ord(s[0]) if s else None))
        # functions/char-transact-sql: "CHAR returns a NULL value for integer expressions outside this input
        # range" (0 to 255); a fraction is cut toward zero as the int conversion does. 128 to 255 depend on the
        # collation's code page, so they aren't drawn.
        n = codes.choice([codes.randint(-5, 127), codes.randint(256, 400), round(codes.uniform(-2.9, 127.9), 1)])
        # + '|' keeps a digit's character from reading back as a number; NULL + '|' is NULL.
        out.append(("CHAR", f"CHAR({n}) + '|'", chr(int(n)) + "|" if 0 <= int(n) <= 127 else None))
    counts = random.Random(SEED + 1)  # its own draws, so the families after it keep their inputs
    for _ in range(40):
        # functions/space-transact-sql and replicate-transact-sql: a negative count gives NULL, and a count
        # with a fraction is made an int first, which cuts it toward zero (data-type-conversion-database-engine).
        n = counts.choice([counts.randint(-3, 4), round(counts.uniform(-2.9, 3.9), 1)])
        whole = int(n)
        out.append(("SPACE", f"SPACE({n})", None if whole < 0 else " " * whole))
        s = counts.choice(WORDS)
        out.append(("REPLICATE", f"REPLICATE({q(s)}, {n})", None if whole < 0 else s * whole))
    for _ in range(80):
        a, b = r.randint(-30, 30), r.choice([x for x in range(-9, 10) if x])
        out.append(("INTDIV", f"{a} / {b}", _tdiv(a, b)))
        # language-elements/modulo-transact-sql: the result takes the sign of the dividend.
        out.append(("MOD", f"{a} % {b}", a - b * _tdiv(a, b)))
        x, places = Decimal(r.randint(-99999, 99999)) / 1000, r.randint(-2, 2)
        dec = f"CAST({x} AS DECIMAL(10,3))"
        out.append(("ROUND", f"ROUND({dec}, {places})", _round(x, places, ROUND_HALF_UP)))
        out.append(("ROUND_TRUNCATE", f"ROUND({dec}, {places}, 1)", _round(x, places, ROUND_DOWN)))
        # functions/cast-and-convert-transact-sql: decimal to int truncates.
        out.append(("CAST_INT", f"CAST({dec} AS INT)", int(x)))
    texts = WORDS + ["a ", "A", "abc ", "b  "]
    for _ in range(80):
        a = r.choice(texts)
        b = a.rstrip(" ") + " " * r.randint(0, 2) if r.random() < 0.3 else r.choice(texts)  # padding counts
        # A warehouse's Latin1_General_100_BIN2_UTF8: code point order, trailing spaces don't count.
        out.append(("EQ", f"CASE WHEN {q(a)} = {q(b)} THEN 1 ELSE 0 END", int(a.rstrip(" ") == b.rstrip(" "))))
        out.append(("LT", f"CASE WHEN {q(a)} < {q(b)} THEN 1 ELSE 0 END",
                    int(a.rstrip(" ").encode() < b.rstrip(" ").encode())))
        s, p = r.choice(WORDS + ["a1", "Abc", "abc  "]), r.choice(
            ["a%", "%c", "_b_", "%", "[a-c]%", "[^a]%", "%[0-9]%", "H%d", "[aA]%", "%_", "__", "a[bB]c%",
             "[ab][^b]%", "%o W%"])
        out.append(("LIKE", f"CASE WHEN {q(s)} LIKE {q(p)} THEN 1 ELSE 0 END", int(_like(s, p))))
    for _ in range(100):
        a = dt.date(2023, 12, 25) + dt.timedelta(days=r.randint(-500, 500))
        b = dt.date(2023, 12, 25) + dt.timedelta(days=r.randint(-500, 500))
        unit, n = r.choice(["year", "quarter", "month", "day", "week"]), r.randint(-14, 14)
        out.append(("DATEDIFF", f"DATEDIFF({unit}, '{a}', '{b}')", _boundaries(unit, a, b)))
        out.append(("DATEADD", f"DATEADD(month, {n}, CAST('{a}' AS DATE))", _add_months(a, n)))
        e = _add_months(a, n)  # functions/eomonth-transact-sql
        out.append(("EOMONTH", f"EOMONTH('{a}', {n})", dt.date(e.year, e.month, calendar.monthrange(e.year, e.month)[1])))
        # functions/datepart-transact-sql with DATEFIRST 7: Sunday is 1; week 1 holds January 1.
        out.append(("WEEKDAY", f"DATEPART(weekday, CAST('{a}' AS DATE))", (a.weekday() + 1) % 7 + 1))
        out.append(("WEEK", f"DATEPART(week, CAST('{a}' AS DATE))", _boundaries("week", dt.date(a.year, 1, 1), a) + 1))
        out.append(("ISO_WEEK", f"DATEPART(iso_week, CAST('{a}' AS DATE))", a.isocalendar()[1]))
    for _ in range(40):
        args = [r.choice(["a", "b", None, ""]) for _ in range(r.randint(2, 4))]
        listed = ", ".join(map(q, args))
        # functions/concat-transact-sql and concat-ws-transact-sql skip NULL; + gives NULL.
        out.append(("CONCAT", f"CONCAT({listed})", "".join(x or "" for x in args)))
        out.append(("CONCAT_WS", f"CONCAT_WS('-', {listed})", "-".join(x for x in args if x is not None)))
        out.append(("PLUS", " + ".join(map(q, args)), None if None in args else "".join(x or "" for x in args)))
    return out


def _value(v: object) -> object:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d\d-\d\d", v):
        return dt.date.fromisoformat(v)
    if isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v):
        return Decimal(v)
    return v


def _same(want: object, got: object) -> bool:
    if isinstance(want, Decimal) or isinstance(got, Decimal):
        return isinstance(got, (int, Decimal)) and isinstance(want, (int, Decimal)) and Decimal(got) == Decimal(want)
    return type(want) is type(got) and want == got


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("tsql-rules") / "app" / "local-data")
    return local.engine(local.create("Rules")["id"])


@pytest.mark.parametrize("prefix", ["", "N"], ids=["varchar", "nvarchar"])
def test_local_tsql_follows_the_documented_rules(engine, prefix: str) -> None:
    unexplained, families_off = [], set()
    for family, expression, want in cases(prefix):
        try:
            got: object = _value(engine.execute(f"SELECT {expression} AS v", dialect="tsql")["rows"][0][0])
        except Exception as exc:  # noqa: BLE001 - silent-ok: an error is an outcome this test names
            got = f"error: {str(exc).splitlines()[0]}"
        if _same(want, got):
            continue
        families_off.add(family)
        if (family, prefix) not in KNOWN:
            unexplained.append(f"{family}: SELECT {expression}  T-SQL: {want!r}  here: {got!r}")
    assert not unexplained, f"{len(unexplained)} answers differ from T-SQL's rules:\n" + "\n".join(unexplained)
    stale = [k for k in KNOWN if k[1] == prefix and k[0] not in families_off]
    assert not stale, f"fixed, so delete from KNOWN: {stale}"


def test_cases_are_seeded_and_cover_every_family() -> None:
    first = cases("")
    assert first == cases("") and len(first) > 1500
    assert {f for f, _, _ in first} >= {f for f, _ in KNOWN} | {"EQ", "LT", "LIKE", "PLUS"}


@pytest.mark.parametrize(("expression", "want"), [
    # T-SQL makes text an int before CHAR, SPACE or REPLICATE use it (data-type-conversion-database-engine).
    ("CHAR('66')", "B"), ("CHAR(' 67 ')", "C"), ("CHAR('300')", None), ("SPACE('2') + '|'", "  |"),
    ("REPLICATE('ab', '3')", "ababab"), ("REPLICATE('ab', '-1')", None),
    ("(SELECT CHAR(c) FROM (VALUES ('66')) AS t(c))", "B"),
    ("(SELECT REPLICATE('ab', c) FROM (VALUES ('2')) AS t(c))", "abab"),
    ("(SELECT SPACE(c) + '|' FROM (VALUES ('1')) AS t(c))", " |"),
])
def test_text_codes_and_counts_are_made_ints_as_tsql_does(engine, expression: str, want: object) -> None:
    assert engine.execute(f"SELECT {expression} AS v", dialect="tsql")["rows"][0][0] == want


@pytest.mark.parametrize("expression", [
    "CHAR('6.5')", "REPLICATE('a', '2.5')", "(SELECT CHAR(c) FROM (VALUES ('6.5')) AS t(c))",
    "(SELECT SPACE(c) FROM (VALUES ('x')) AS t(c))", "SPACE(1e400)",
])
def test_text_that_is_not_a_whole_number_stops_as_in_tsql(engine, expression: str) -> None:
    # T-SQL: "Conversion failed when converting the varchar value '6.5' to data type int."; a float too
    # large for an int is an overflow there.
    with pytest.raises(Exception, match=r"Conversion failed when converting the varchar value|out of range"):
        engine.execute(f"SELECT {expression} AS v", dialect="tsql")


def test_upper_keeps_sharp_s(engine) -> None:
    # Unicode's simple case mapping (UnicodeData.txt) gives U+00DF no one-letter capital, and T-SQL's UPPER
    # keeps it: UPPER(N'straße') is N'STRAßE'. DuckDB alone gave the capital U+1E9E.
    rows = engine.execute("SELECT UPPER(N'straße') AS a, UPPER(x) AS b FROM (VALUES (N'ßaß'), (N''), (NULL), (N'Grüße'))"
                          " AS t(x)", dialect="tsql")["rows"]
    assert [tuple(r) for r in rows] == [("STRAßE", "ßAß"), ("STRAßE", ""), ("STRAßE", None), ("STRAßE", "GRÜßE")]
    one = engine.execute("SELECT UPPER(5) AS a, UPPER(N'abc') AS b, UPPER(N'ẞ') AS c", dialect="tsql")["rows"][0]
    assert list(one) == ["5", "ABC", "ẞ"]


def test_upper_reads_its_text_once() -> None:
    from fabric_desktop.localdata import tsql
    assert tsql.translate("SELECT UPPER(LTRIM(x)) FROM t")[0][-1].upper().count("LTRIM(") == 1

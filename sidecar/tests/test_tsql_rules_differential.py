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
    mixed = random.Random(SEED + 3)  # its own draws, so the families above keep their inputs
    for _ in range(40):
        # data-types/data-type-precedence-transact-sql: text meeting an int is converted to the int, and
        # expressions/case-transact-sql, coalesce-transact-sql and set-operators-union-transact-sql take the type of
        # highest precedence; text for an int is a whole number with spaces around it, '' being 0.
        t, n = mixed.choice(["", " 7 ", "10", "9", "-3", "+4", "007", " 12"]), mixed.randint(-12, 12)
        whole = int(t) if t.strip() else 0
        out.append(("CASE_TEXT", f"CASE WHEN {n} > 99 THEN {n} ELSE {q(t)} END", whole))
        out.append(("IIF_TEXT", f"IIF({n} > 99, {n}, {q(t)})", whole))
        out.append(("COALESCE_TEXT", f"COALESCE(NULL, {q(t)}, {n})", whole))
        # MAX of an int column is the larger number; of a text column, the later text ('9' after '10').
        out.append(("UNION_TEXT", f"(SELECT MAX(a) FROM (SELECT {n} AS a UNION ALL SELECT {q(t)}) AS u)", max(n, whole)))
        out.append(("EXCEPT_TEXT", f"(SELECT COUNT(*) FROM (SELECT {whole} AS a EXCEPT SELECT {q(t)}) AS u)", 0))
    return out + _regex_cases(random.Random(SEED + 3), q)


def _regex(pattern: str, flags: str) -> re.Pattern[str]:
    # functions/regexp-like-transact-sql: "m Multi-line mode: ^ and $ match begin/end line in addition to
    # begin/end text", "s Let . match \\n", c and i: "SQL Server uses the last character". Python's $ also
    # matches before a final line break, so without m it is written \Z here.
    mode = (re.M if "m" in flags else 0) | (re.S if "s" in flags else 0)
    case = [c for c in flags if c in "ci"]
    mode |= re.I if case and case[-1] == "i" else 0
    return re.compile(pattern if "m" in flags else pattern.replace("$", r"\Z"), mode)


def _regex_cases(r: random.Random, q) -> list[tuple[str, str, object]]:
    """WR-257: REGEXP_LIKE (a match anywhere, as the reference's examples use it), REGEXP_REPLACE (every
    occurrence by default) and REGEXP_COUNT and REGEXP_INSTR, whose "Return value" is int, so / 2 divides as
    integers. The subjects hold line breaks, written with CHAR(10)."""
    out: list[tuple[str, str, object]] = []
    subjects = [["a", "b"], ["ab", "ab"], ["x", "a"], ["A", "b"], ["abc"], ["b", "a", "b"]]
    for _ in range(80):
        lines, flags = r.choice(subjects), r.choice(["c", "m", "s", "ms", "sm", "i", "mi", "im", "ic", "ci", "smi"])
        p = r.choice(["^b", "a$", "^a.*b$", "a.b", "b$", "^a", "a.", "^A", "(a|b)$"])
        s, text = "\n".join(lines), " + CHAR(10) + ".join(q(x) for x in lines)
        rx = _regex(p, flags)
        out.append(("REGEXP_LIKE", f"CASE WHEN REGEXP_LIKE({text}, {q(p)}, {q(flags)}) THEN 1 ELSE 0 END",
                    int(rx.search(s) is not None)))
        out.append(("REGEXP_REPLACE", f"REGEXP_REPLACE({text}, {q(p)}, {q('X')}, 1, 0, {q(flags)})", rx.sub("X", s)))
        found = list(rx.finditer(s))
        out.append(("REGEXP_COUNT", f"REGEXP_COUNT({text}, {q(p)}, 1, {q(flags)})", len(found)))
        out.append(("REGEXP_COUNT", f"REGEXP_COUNT({text}, {q(p)}) / 2", len(list(re.finditer(_regex(p, "c"), s))) // 2))
        out.append(("REGEXP_INSTR", f"REGEXP_INSTR({text}, {q(p)}, 1, 1, 0, {q(flags)}) / 2",
                    (found[0].start() + 1 if found else 0) // 2))
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


@pytest.mark.parametrize("expression", [
    "CASE WHEN 1 = 0 THEN 1 ELSE '2.5' END", "IIF(1 = 0, 1, 'a')", "COALESCE(NULL, 'x', 1)",
    "(SELECT MAX(a) FROM (SELECT 1 AS a UNION ALL SELECT '2.5') AS u)",
    "(SELECT MAX(a) FROM (SELECT 'abc' AS a UNION SELECT 1) AS u)",
])
def test_text_that_isnt_a_whole_number_stops_where_it_meets_an_int(engine, expression: str) -> None:
    # data-types/data-type-precedence-transact-sql: the text is converted to int, and '2.5' or 'a' can't be:
    # "Conversion failed when converting the varchar value '2.5' to data type int." DuckDB rounded '2.5' to 3, and a
    # UNION of an int and text was a text column.
    with pytest.raises(Exception, match="Conversion failed when converting"):
        engine.execute(f"SELECT {expression} AS v", dialect="tsql")


def test_text_columns_meeting_int_columns_take_the_int_type(engine) -> None:
    engine.execute("CREATE TABLE dbo.mixed (i INT, s VARCHAR(10), b BIT, d DATE)", dialect="tsql")
    engine.execute("INSERT INTO dbo.mixed VALUES (1, '10', 1, '2024-01-01'), (2, ' 9 ', 0, '2024-01-02'), "
                   "(3, '', NULL, NULL)", dialect="tsql")

    def rows(query: str) -> list[tuple]:
        return [tuple(_value(v) for v in r) for r in engine.execute(query, dialect="tsql")["rows"]]
    # CASE and COALESCE take the int (DuckDB stopped: "Cannot mix values of type VARCHAR and INTEGER").
    assert rows("SELECT i, CASE WHEN i > 1 THEN i ELSE s END FROM dbo.mixed ORDER BY i") == [(1, 10), (2, 2), (3, 3)]
    assert rows("SELECT i, COALESCE(s, i) FROM dbo.mixed ORDER BY i") == [(1, 10), (2, 9), (3, 0)]
    # A UNION's column is an int, so it sorts as numbers (here it sorted as text: '', ' 9 ', '1', '10', ...).
    assert rows("SELECT a FROM (SELECT i AS a FROM dbo.mixed UNION ALL SELECT s FROM dbo.mixed) AS x ORDER BY a") == [
        (0,), (1,), (2,), (3,), (9,), (10,)]
    assert rows("SELECT a FROM (SELECT s AS a FROM dbo.mixed UNION SELECT i FROM dbo.mixed) AS x ORDER BY a") == [
        (0,), (1,), (2,), (3,), (9,), (10,)]
    # bit outranks text ('true' is 1), and so does date.
    assert rows("SELECT a FROM (SELECT b AS a FROM dbo.mixed UNION ALL SELECT 'true') AS x ORDER BY a") == [
        (None,), (False,), (True,), (True,)]
    assert rows("SELECT a FROM (SELECT d AS a FROM dbo.mixed WHERE i = 1 UNION ALL SELECT '2024-03-04') AS x "
                "ORDER BY a") == [(dt.date(2024, 1, 1),), (dt.date(2024, 3, 4),)]
    # ' 1' is the int 1, so EXCEPT takes it away (here the text '1' and ' 1' differed, and 1 was kept).
    assert rows("SELECT 1 AS a EXCEPT SELECT ' 1'") == []
    # An unnamed first column is converted too, under DuckDB's name for it: '5' and 10 sort as numbers.
    result = engine.execute("SELECT '5' UNION ALL SELECT 10 ORDER BY 1", dialect="tsql")
    assert [tuple(_value(v) for v in r) for r in result["rows"]] == [(5,), (10,)]
    assert rows("SELECT ' 1' EXCEPT SELECT 1") == []
    # Arithmetic on a CASE, IIF or COALESCE of text and an int (it stopped with DuckDB's binder error).
    assert rows("SELECT CASE WHEN 1 = 1 THEN '7' ELSE 2 END + 1, IIF(1 = 1, '7', 2) + 1, COALESCE('7', 2) + 1, "
                "COALESCE(s, i) + 1 FROM dbo.mixed WHERE i = 2") == [(8, 8, 8, 10)]


@pytest.mark.parametrize("frame", ["RANGE BETWEEN 1 PRECEDING AND CURRENT ROW", "RANGE 2 PRECEDING",
                                   "RANGE BETWEEN CURRENT ROW AND 1 FOLLOWING"])
def test_range_with_a_number_of_rows_is_refused(engine, frame: str) -> None:
    # queries/select-over-clause-transact-sql: "<unsigned value specification> PRECEDING ... This specification
    # isn't allowed for RANGE." DuckDB ran it as a range of values.
    with pytest.raises(ValueError, match="RANGE with a number of rows .* nothing ran.* Use ROWS BETWEEN"):
        engine.execute(f"SELECT SUM(v) OVER (ORDER BY k {frame}) AS s FROM (VALUES (1, 1), (2, 2)) AS t(k, v)",
                       dialect="tsql")


def test_windows_with_order_and_no_frame_sum_the_ties_together(engine) -> None:
    # queries/select-over-clause-transact-sql: "If ORDER BY is specified and ROWS/RANGE is not specified, then
    # RANGE UNBOUNDED PRECEDING AND CURRENT ROW is used as default for window frame"; NULLs sort first.
    rows = engine.execute("SELECT k, v, SUM(v) OVER (ORDER BY k) AS s, LAST_VALUE(v) OVER (ORDER BY k) AS l, "
                          "COUNT(*) OVER (ORDER BY k RANGE UNBOUNDED PRECEDING) AS c, SUM(v) OVER () AS t FROM "
                          "(VALUES (1, 10), (2, 20), (2, 20), (NULL, 5), (4, NULL)) AS q(k, v) ORDER BY k, v",
                          dialect="tsql")["rows"]
    assert [tuple(_value(r[i]) for i in (0, 2, 3, 4, 5)) for r in rows] == [
        (None, 5, 5, 1, 55), (1, 15, 10, 2, 55), (2, 55, 20, 4, 55), (2, 55, 20, 4, 55), (4, 55, None, 5, 55)]


def test_isnull_of_a_number_too_long_for_its_text_stops(engine) -> None:
    # functions/isnull-transact-sql: the value is converted to the first argument's type, as CAST(12345 AS
    # VARCHAR(2)) is, which doesn't cut a number short (here it gave '12').
    with pytest.raises(Exception, match="doesn't fit in VARCHAR"):
        engine.execute("SELECT ISNULL(CAST(NULL AS VARCHAR(2)), 12345) AS v", dialect="tsql")
    assert engine.execute("SELECT ISNULL(CAST(NULL AS VARCHAR(5)), 12345) AS v", dialect="tsql")["rows"][0][0] == "12345"


@pytest.mark.parametrize(("expression", "want"), [
    # T-SQL makes text an int before CHAR, SPACE or REPLICATE use it (data-type-conversion-database-engine).
    ("CHAR('66')", "B"), ("CHAR(' 67 ')", "C"), ("CHAR('300')", None), ("SPACE('2') + '|'", "  |"),
    ("REPLICATE('ab', '3')", "ababab"), ("REPLICATE('ab', '-1')", None),
    ("(SELECT CHAR(c) FROM (VALUES ('66')) AS t(c))", "B"),
    ("(SELECT REPLICATE('ab', c) FROM (VALUES ('2')) AS t(c))", "abab"),
    ("(SELECT SPACE(c) + '|' FROM (VALUES ('1')) AS t(c))", " |"),
    # '' and spaces alone convert to 0 (CAST('' AS INT) is 0 in T-SQL)
    ("REPLICATE('ab', '') + '|'", "|"), ("SPACE('  ') + '|'", "|"), ("ASCII(CHAR(''))", 0),
    ("(SELECT REPLICATE('ab', c) + '|' FROM (VALUES ('')) AS t(c))", "|"),
    ("CHAR(-2147483648)", None), ("REPLICATE('a', 2147483647 - 2147483647)", ""),
])
def test_text_codes_and_counts_are_made_ints_as_tsql_does(engine, expression: str, want: object) -> None:
    assert engine.execute(f"SELECT {expression} AS v", dialect="tsql")["rows"][0][0] == want


@pytest.mark.parametrize("expression", [
    "CHAR('6.5')", "(SELECT CHAR(c) FROM (VALUES ('6.5')) AS t(c))",
    "(SELECT SPACE(c) FROM (VALUES ('x')) AS t(c))", "SPACE(1e400)", "CHAR('+')",
    # past an int (CHAR, SPACE) or a bigint (REPLICATE): T-SQL's overflow error
    "CHAR('9999999999')", "CHAR(1e10)", "CHAR(-2147483649)", "SPACE(2147483648)",
    "(SELECT CHAR(c) FROM (VALUES ('9999999999')) AS t(c))", "(SELECT SPACE(c) FROM (VALUES (3000000000)) AS t(c))",
    "REPLICATE('a', 9223372036854775808)",
    # more digits than Python's int() reads (4,300) or a float holds: still the overflow error, not Python's own
    "CHAR(1" + "0" * 5000 + ")", "SPACE('1" + "0" * 5000 + "')", "REPLICATE('a', '1" + "0" * 5000 + "')",
])
def test_text_that_is_not_a_whole_number_stops_as_in_tsql(engine, expression: str) -> None:
    # T-SQL: "Conversion failed when converting the varchar value '6.5' to data type int."; a float too
    # large for an int is an overflow there.
    with pytest.raises(Exception, match=r"Conversion failed when converting the varchar value|out of range|Arithmetic overflow"):
        engine.execute(f"SELECT {expression} AS v", dialect="tsql")


@pytest.mark.parametrize("expression", ["CHAR(RAND() * 300)", "REPLICATE('a', RAND() * 6 - 3)", "SPACE(RAND() * 4)"])
def test_a_computed_code_or_count_is_read_once(expression: str) -> None:
    from fabric_desktop.localdata import tsql
    assert tsql.translate(f"SELECT {expression}")[0][-1].upper().count("RANDOM()") == 1


def test_nested_codes_and_counts_grow_the_sql_linearly() -> None:
    from fabric_desktop.localdata import tsql
    sizes = []
    for depth in range(1, 6):
        expression = "x"
        for _ in range(depth):
            expression = f"CHAR(ASCII(REPLICATE({expression}, x)))"
        sizes.append(len(tsql.translate(f"SELECT {expression} FROM (VALUES ('1')) AS t(x)")[0][-1]))
    steps = [b - a for a, b in zip(sizes, sizes[1:], strict=False)]
    assert max(steps) <= 1.2 * min(steps), sizes


def test_a_call_nested_in_a_count_is_rewritten_too(engine) -> None:
    got = engine.execute("SELECT REPLICATE(REPLICATE('ab', c), c) AS v FROM (VALUES ('2'), ('-1')) AS t(c)",
                         dialect="tsql")["rows"]
    assert [r[0] for r in got] == ["abababab", None]


def test_replicate_takes_a_bigint_count(engine) -> None:
    # REPLICATE takes "any integer type, including bigint" (functions/replicate-transact-sql): a count past an
    # int runs on VARCHAR(MAX) text, text is made a bigint, and a count past a bigint is the overflow error.
    def one(sql: str) -> object:
        return engine.execute(f"SELECT {sql} AS v", dialect="tsql")["rows"][0][0]
    assert one("REPLICATE(CAST('' AS VARCHAR(MAX)), 3000000000)") == ""
    assert one("(SELECT REPLICATE(CAST('' AS VARCHAR(MAX)), c) FROM (VALUES (3000000000)) AS t(c))") == ""
    assert one("LEN(REPLICATE(CAST('ab' AS VARCHAR(MAX)), '3'))") == 6
    assert one("LEN(REPLICATE(CAST('a' AS VARCHAR(MAX)), " + "0" * 5000 + "3))") == 3  # a number literal too
    assert one("REPLICATE('x', '')") == ""  # '' is 0, as SQL Server's CAST('' AS INT)
    assert one("LEN(REPLICATE(CAST('a' AS VARCHAR(MAX)), '" + "0" * 5000 + "3'))") == 3  # leading zeros
    with pytest.raises(Exception, match="Error converting data type varchar to bigint"):
        one("REPLICATE('a', '2.5')")
    # Text that isn't VARCHAR(MAX) is cut at 8,000 bytes in a warehouse, so a long result is refused by name.
    with pytest.raises(Exception, match="8,000 bytes"):
        one("REPLICATE('a', 3000000000)")


def test_space_of_a_negative_count_is_null(engine) -> None:
    # Checked against SQL Server: SPACE(-1) is NULL, as REPLICATE of a negative count is.
    got = engine.execute("SELECT SPACE(-1) AS a, SPACE(c) AS b FROM (VALUES (-2)) AS t(c)", dialect="tsql")["rows"]
    assert list(got[0]) == [None, None]


def test_upper_keeps_sharp_s(engine) -> None:
    # Unicode's simple case mapping (UnicodeData.txt) gives U+00DF no one-letter capital, and T-SQL's UPPER
    # keeps it: UPPER(N'straße') is N'STRAßE'. DuckDB alone gave the capital U+1E9E.
    rows = engine.execute("SELECT UPPER(N'straße') AS a, UPPER(x) AS b FROM (VALUES (N'ßaß'), (N''), (NULL), (N'Grüße'))"
                          " AS t(x)", dialect="tsql")["rows"]
    assert [tuple(r) for r in rows] == [("STRAßE", "ßAß"), ("STRAßE", ""), ("STRAßE", None), ("STRAßE", "GRÜßE")]
    one = engine.execute("SELECT UPPER(5) AS a, UPPER(N'abc') AS b, UPPER(N'ẞ') AS c", dialect="tsql")["rows"][0]
    assert list(one) == ["5", "ABC", "ẞ"]
    nested = engine.execute("SELECT UPPER(UPPER(N'ß')) AS a, UPPER(LOWER(UPPER(x))) AS b FROM (VALUES (N'ßa')) AS t(x)",
                            dialect="tsql")["rows"][0]
    assert list(nested) == ["ß", "ßA"]


def test_upper_reads_its_text_once() -> None:
    from fabric_desktop.localdata import tsql
    assert tsql.translate("SELECT UPPER(LTRIM(x)) FROM t")[0][-1].upper().count("LTRIM(") == 1


def test_a_table_level_primary_key_names_its_columns(engine) -> None:
    # sqlglot's T-SQL reader wrote PRIMARY KEY (a NULLS FIRST), which DuckDB can't read.
    engine.execute("CREATE TABLE dbo.keyed (a INT NOT NULL, b INT NOT NULL, CONSTRAINT pk_keyed PRIMARY KEY (a, b DESC))",
                   dialect="tsql")
    engine.execute("INSERT INTO dbo.keyed VALUES (1, 1), (1, 2)", dialect="tsql")
    with pytest.raises(Exception, match="(?i)duplicate key|constraint"):
        engine.execute("INSERT INTO dbo.keyed VALUES (1, 2)", dialect="tsql")
    assert engine.execute("SELECT COUNT(*) AS n FROM dbo.keyed", dialect="tsql")["rows"][0][0] == 2


@pytest.mark.parametrize(("style", "want"), [("108", "10:04:05"), ("120", "1900-01-01 10:04:05")])
def test_convert_of_a_time_with_a_style(engine, style: str, want: str) -> None:
    # DuckDB's STRFTIME takes no time alone; T-SQL puts a time on its base date, 1900-01-01.
    sql = f"SELECT CONVERT(VARCHAR(30), CAST('10:04:05' AS TIME(0)), {style}) AS v"
    assert engine.execute(sql, dialect="tsql")["rows"][0][0] == want


@pytest.mark.parametrize("call", ["LEFT('abc', {0})", "RIGHT('abc', {0})"])
@pytest.mark.parametrize("length", ["9" * 400, "9223372036854775808", "1e400"])
def test_left_and_right_past_a_bigint_are_the_overflow_error(engine, call: str, length: str) -> None:
    # LEFT and RIGHT take a bigint length; past one is T-SQL's overflow error, not Python's OverflowError.
    with pytest.raises(ValueError, match="Arithmetic overflow error converting expression to data type bigint"):
        engine.execute(f"SELECT {call.format(length)} AS v", dialect="tsql")


def test_left_with_a_long_bigint_length_keeps_the_whole_text(engine) -> None:
    assert engine.execute("SELECT LEFT('abc', 9223372036854775807) AS v", dialect="tsql")["rows"][0][0] == "abc"
    rows = engine.execute("SELECT LEFT(s, n) AS a, RIGHT(s, n) AS b FROM (VALUES ('abc', CAST(5000000000 AS BIGINT)))"
                          " AS t(s, n)", dialect="tsql")["rows"]
    assert list(rows[0]) == ["abc", "abc"]

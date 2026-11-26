"""WR-275: text and numbers read as dates the way T-SQL reads them, with DATEFORMAT mdy (us_english), as a Fabric
Warehouse does.

Microsoft Learn's "date (Transact-SQL)" lists the string literal formats T-SQL takes. DuckDB reads only ISO text
('2024-01-15'), so a query with '20240115', '01/15/2024' or 'Jan 15 2024' stopped with DuckDB's error, or gave NULL
in TRY_CAST and TRY_CONVERT. The shapes carried here are:

- unseparated: 'yyyymmdd' and 'yymmdd' ("A six-digit or eight-digit string is always interpreted as ymd");
- numeric with /, - or .: 'yyyy-mm-dd' (ymd) and 'm/d/yyyy' or 'm/d/yy' (mdy);
- alphabetical: 'Jan 15 2024', 'Jan 15, 2024', '15 Jan 2024', 'January 15, 2024';
- each with an optional time after a space (hh:mm, hh:mm:ss, hh:mm:ss.fffffff, with AM or PM), a time alone (on
  1900-01-01), and '' (1900-01-01).

A two-digit year takes T-SQL's default two digit year cutoff, 2049: 49 is 2049 and 50 is 1950. Text written in the
query is rewritten to ISO text when the query is translated; text from a column is rewritten the same way in the
generated SQL, read once. Text with a digit in another shape is refused by name (a T-SQL reading of it can't be
told), so a TRY_ form gives NULL only for text that isn't a date. A shape that matches with a day, month or time
that doesn't exist is T-SQL's conversion error (NULL with TRY_).
"""

from __future__ import annotations

import datetime as dt
import functools
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import sqlglot
from sqlglot import exp

_NOT_YET = "This T-SQL can't be run on the local engine: "
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
# Longer names first; T-SQL's us_english takes each month's full name and its first three letters.
_MONTH = "(" + "|".join(sorted({m for m in _MONTHS} | {m[:3] for m in _MONTHS}, key=lambda m: (-len(m), m))) + ")"
# No backslashes, so each pattern reads the same in Python and in DuckDB's RE2, and in a SQL string.
_TIME = ("([0-9]{1,2}):([0-9]{2})(?::([0-9]{2})(?:[.]([0-9]+))?)?(?:[ ]*(am|pm))?")
_AFTER = f"(?:[ ]+{_TIME})?"
_COMMA = "(?:[ ]*,[ ]*|[ ]+)"
# (pattern, the role of each of its first three groups: y = four-digit year, yy = two-digit year, m, d, mon)
_SHAPES: list[tuple[str, tuple[str, str, str]]] = [
    ("([0-9]{4})([0-9]{2})([0-9]{2})", ("y", "m", "d")),
    ("([0-9]{2})([0-9]{2})([0-9]{2})", ("yy", "m", "d")),
    *[(f"([0-9]{{4}})[{sep}]([0-9]{{1,2}})[{sep}]([0-9]{{1,2}})", ("y", "m", "d")) for sep in "-/."],
    *[(f"([0-9]{{1,2}})[{sep}]([0-9]{{1,2}})[{sep}]([0-9]{{4}}|[0-9]{{2}})", ("m", "d", "yy")) for sep in "-/."],
    (f"{_MONTH}[ ]+([0-9]{{1,2}}){_COMMA}([0-9]{{4}})", ("mon", "d", "y")),
    (f"([0-9]{{1,2}})[ ]+{_MONTH}{_COMMA}([0-9]{{4}})", ("d", "mon", "y")),
]
# ISO 8601 with its T (and a Z for UTC), which DuckDB reads as T-SQL does.
_ISO_T = "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:[.][0-9]{1,7})?)?Z?"
# Words DuckDB reads as dates that T-SQL doesn't.
_DUCKDB_WORDS = ("epoch", "infinity", "-infinity", "+infinity", "allballs", "today", "now", "yesterday", "tomorrow")
NOT_A_DATE = "not a date"  # text DuckDB refuses as a date, for what T-SQL refuses too
TARGETS = {exp.DataType.Type.DATE, exp.DataType.Type.DATETIME, exp.DataType.Type.DATETIME2,
           exp.DataType.Type.SMALLDATETIME, exp.DataType.Type.TIMESTAMP}


def refusal(text: str) -> str:
    return (f"{_NOT_YET}'{text[:60]}' is read as a date here, and T-SQL's reading of text in that shape isn't "
            "carried yet. Write the date as 'YYYY-MM-DD' (or 'YYYYMMDD'), with any time after it as 'hh:mm:ss', "
            "or use CONVERT with a style, such as CONVERT(date, x, 103) for dd/mm/yyyy.")


def _year(text: str) -> int:
    n = int(text)
    return n if len(text) == 4 else (2000 + n if n < 50 else 1900 + n)  # the two digit year cutoff, 2049


def _hour(hour: int, half: str) -> int | None:
    if not half:
        return hour
    if not 1 <= hour <= 12:
        return None
    return hour % 12 + (12 if half.lower() == "pm" else 0)


def literal(text: str) -> str | None:
    """``text`` as ISO text T-SQL reads the same way, or NOT_A_DATE for a shape whose date or time doesn't exist;
    None to leave it as written (ISO with its T, or text with no digit). Raises ValueError for another shape."""
    t = text.strip(" ")
    if t == "":
        return "1900-01-01"
    if re.fullmatch(_ISO_T, t):
        return None
    if not any(c.isdigit() for c in t):
        return NOT_A_DATE if t.lower() in _DUCKDB_WORDS else None
    if found := re.fullmatch(_TIME, t, re.IGNORECASE):
        return _written(1900, 1, 1, found.groups())
    for pattern, roles in _SHAPES:
        if found := re.fullmatch(pattern + _AFTER, t, re.IGNORECASE):
            parts = dict(zip(roles, found.groups()[:3], strict=True))
            year = _year(parts.get("y") or parts["yy"])
            mon = parts.get("mon")
            month = [m[:3] for m in _MONTHS].index(mon[:3].lower()) + 1 if mon else int(parts["m"])
            return _written(year, month, int(parts["d"]), found.groups()[3:])
    raise ValueError(refusal(text))


def _written(year: int, month: int, day: int, time: tuple[str | None, ...]) -> str:
    """The ISO text; a day that doesn't exist is left in it ('2024-02-30'), for the error ISO text gives there."""
    out = f"{year:04d}-{month:02d}-{day:02d}"
    hour_text, minute, second, fraction, half = time
    if hour_text is None:
        return out
    hour = _hour(int(hour_text), half or "")
    if hour is None or hour > 23 or int(minute or 0) > 59 or int(second or 0) > 59:
        return NOT_A_DATE
    return f"{out} {hour:02d}:{minute}:{second or '00'}" + (f".{fraction}" if fraction else "")


# ---------- text from a column, in the generated SQL

_V = "__kf_date_text"
_P = "__kf_date_parts"
_HOLE = "__kf_date_value"


def _canonical() -> list[tuple[str, str]]:
    """(anchored pattern, replacement) for each shape: the replacement writes year|month|day|hour|minute|second|
    fraction|am-or-pm, with '' for a part that isn't there, so one assembly reads every shape."""
    def refs(groups: list[int]) -> str:
        return "|".join(f"\\{i}" for i in groups)
    out = [(f"(?i)^[ ]*{_TIME}[ ]*$", "1900|1|1|" + refs(list(range(1, 6))))]
    for pattern, roles in _SHAPES:
        at = {role: i + 1 for i, role in enumerate(roles)}
        order = [at.get("y") or at["yy"], at.get("m") or at["mon"], at["d"], *range(4, 9)]
        out.append((f"(?i)^[ ]*{pattern}{_AFTER}[ ]*$", refs(order)))
    return out


def _assembly() -> str:
    """DuckDB SQL for the ISO text of the eight parts in the list ``__kf_date_parts``."""
    y, m, d, h, mi, s, f, half = (f"{_P}[{i}]" for i in range(1, 9))
    names = ", ".join(f"'{name[:3]}'" for name in _MONTHS)
    year = (f"CASE WHEN LENGTH({y}) = 2 THEN (CASE WHEN CAST({y} AS INTEGER) < 50 THEN '20' ELSE '19' END) || {y} "
            f"ELSE {y} END")
    month = (f"CASE WHEN REGEXP_FULL_MATCH({m}, '[0-9]+') THEN {m} "
             f"ELSE CAST(LIST_POSITION([{names}], LEFT(LOWER({m}), 3)) AS VARCHAR) END")
    date = f"({year}) || '-' || LPAD({month}, 2, '0') || '-' || LPAD({d}, 2, '0')"
    hour = (f"CASE WHEN {half} = '' THEN CAST({h} AS INTEGER) WHEN CAST({h} AS INTEGER) NOT BETWEEN 1 AND 12 THEN 99 "
            f"WHEN LOWER({half}) = 'pm' THEN CAST({h} AS INTEGER) % 12 + 12 ELSE CAST({h} AS INTEGER) % 12 END")
    second = f"COALESCE(NULLIF({s}, ''), '00')"
    bad = f"({hour}) > 23 OR CAST({mi} AS INTEGER) > 59 OR CAST({second} AS INTEGER) > 59"
    time = (f"' ' || LPAD(CAST({hour} AS VARCHAR), 2, '0') || ':' || {mi} || ':' || {second} || "
            f"CASE WHEN {f} = '' THEN '' ELSE '.' || {f} END")
    return f"CASE WHEN {h} = '' THEN {date} WHEN {bad} THEN '{NOT_A_DATE}' ELSE {date} || {time} END"


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


@functools.cache
def _column_sql() -> str:
    t = f"TRIM({_V}, ' ')"
    shapes = _canonical()
    canon = " ".join(f"WHEN REGEXP_MATCHES({_V}, {_quoted(pat)}) THEN REGEXP_REPLACE({_V}, {_quoted(pat)}, "
                     f"{_quoted(rep)})" for pat, rep in shapes)
    words = ", ".join(_quoted(w) for w in _DUCKDB_WORDS)
    before, after = refusal("\0").split("\0")
    refused = f"ERROR({_quoted(before)} || LEFT({_V}, 60) || {_quoted(after)})"
    parts = f"LIST_TRANSFORM([STRING_SPLIT(CASE {canon} END, '|')], {_P} -> {_assembly()})[1]"
    shaped = " OR ".join(f"REGEXP_MATCHES({_V}, {_quoted(pat)})" for pat, _ in shapes)
    return (f"LIST_TRANSFORM([{_HOLE}], {_V} -> CASE WHEN {t} = '' THEN '1900-01-01' "
            f"WHEN REGEXP_FULL_MATCH({t}, {_quoted(_ISO_T)}) THEN {t} "
            f"WHEN LOWER({t}) IN ({words}) THEN '{NOT_A_DATE}' "
            f"WHEN NOT REGEXP_MATCHES({_V}, '[0-9]') THEN {_V} "
            f"WHEN {shaped} THEN {parts} "
            f"ELSE CAST({refused} AS VARCHAR) END)[1]")


def column(value: exp.Expression) -> exp.Expr:
    """``value`` (text, in the tree) replaced in place by the ISO text T-SQL reads it as: what ``literal`` does,
    in DuckDB SQL, with ``value`` read once. Text with a digit in another shape stops with the refusal."""
    out = sqlglot.parse_one(_column_sql(), read="duckdb")
    hole = next(c for c in out.find_all(exp.Column) if c.name == _HOLE)
    value.replace(out)
    hole.replace(value)
    return out


# ---------- a number read as a datetime

_TICKS_A_DAY = 300 * 86400  # datetime keeps 1/300 of a second
_BASE = dt.datetime(1900, 1, 1)
RANGES = {exp.DataType.Type.DATETIME: (dt.datetime(1753, 1, 1), dt.datetime(9999, 12, 31, 23, 59, 59, 997000)),
          exp.DataType.Type.SMALLDATETIME: (dt.datetime(1900, 1, 1), dt.datetime(2079, 6, 6, 23, 59))}


def number_text(written: str) -> str | None:
    """A number as T-SQL converts it to a datetime, that many days after 1900-01-01 with a fraction as part of a
    day, rounded to 1/300 of a second, written as datetime text; None past datetime's range. ``written`` is the
    number as the query has it, a minus sign included."""
    try:
        days = Decimal(written)
    except InvalidOperation:
        return None
    ticks = int((days * _TICKS_A_DAY).to_integral_value(rounding=ROUND_HALF_UP))
    whole, within = divmod(ticks, _TICKS_A_DAY)
    if not -53690 <= whole <= 2958463:  # 1753-01-01 to 9999-12-31, as days from 1900-01-01
        return None
    ms = (within * 10 + 1) // 3  # as T-SQL shows a tick: .000, .003, .007
    value = _BASE + dt.timedelta(days=whole, milliseconds=ms)
    if not RANGES[exp.DataType.Type.DATETIME][0] <= value <= RANGES[exp.DataType.Type.DATETIME][1]:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S.") + f"{value.microsecond // 1000:03d}"

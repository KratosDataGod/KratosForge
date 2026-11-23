"""Spark's text for an interval, for CAST(... AS STRING) and for || and concat with text, on this PC.

Spark 3.5 writes an ANSI interval as an interval literal of its type: ``INTERVAL '05' SECOND``,
``INTERVAL '1 12:30:01.001001' DAY TO SECOND``, ``INTERVAL '1-2' YEAR TO MONTH`` (IntervalUtils
toDayTimeIntervalString and toYearMonthIntervalString), and make_interval's calendar interval as
``1 years 2 months 25 days 5 hours 6 minutes 7.5 seconds`` (CalendarInterval.toString). DuckDB has one
INTERVAL type and writes '1 day 12:30:01.001001', so the cast gave other text.

The text depends on the interval's fields (DAY TO SECOND, HOUR, YEAR TO MONTH...), which DuckDB's
type doesn't carry, so they are read from the expression being cast: an interval literal, make_dt_interval,
make_interval, a subtraction of timestamps or dates, and sums, differences and negations of these. An
interval whose fields can't be read from the query (a column, a CASE, a function's result) is refused by
name when it is cast to text, so nothing runs with DuckDB's text in its place.
"""

from __future__ import annotations

import re
from typing import Any

from sqlglot import exp

_DAY_TIME = ("DAY", "HOUR", "MINUTE", "SECOND")
_YEAR_MONTH = ("YEAR", "MONTH")
# Spark's type for an interval literal of one unit: weeks are days, milli- and microseconds are seconds.
_UNITS = {"YEAR": "YEAR", "MONTH": "MONTH", "WEEK": "DAY", "DAY": "DAY", "HOUR": "HOUR", "MINUTE": "MINUTE",
          "SECOND": "SECOND", "MILLISECOND": "SECOND", "MICROSECOND": "SECOND"}
# A literal such as INTERVAL '1 day 2 hours': numbers, each with its unit
_PARTS = re.compile(r"\s*[+-]?\d+(?:\.\d+)?\s+([A-Za-z]+)")

Kind = tuple[str, str, str]  # ("dt" | "ym", first field, last field), or ("cal", "", "") for make_interval
CALENDAR: Kind = ("cal", "", "")
MIXED: Kind = ("mixed", "", "")

REFUSED = ("CAST of an interval to string can''t run on this PC the way it runs in Fabric, because Spark writes "
           "an interval by its fields (such as INTERVAL ''1 12:00:00'' DAY TO SECOND) and this PC can''t tell "
           "them from the query here, so nothing ran. Cast the interval where it is made, for example "
           "CAST(make_dt_interval(1, 12) AS STRING) or CAST(end_time - start_time AS STRING), or run the cell in Fabric.")
_MIXED = ("CAST of an interval to string can't run on this PC the way it runs in Fabric, because this interval mixes "
          "years or months with days or times, which Spark 3.5 stops on, so nothing ran. Use an interval of "
          "years and months or one of days and times, or run the cell in Fabric.")


def _unit(name: str) -> tuple[str, str] | None:
    """("dt" | "ym", Spark's field) for a unit's name, singular or plural, or None."""
    name = name.upper()
    field = _UNITS.get(name) or (_UNITS.get(name[:-1]) if name.endswith("S") else None)
    if field is None:
        return None
    return ("ym" if field in _YEAR_MONTH else "dt"), field


def _span(fields: list[tuple[str, str]]) -> Kind | None:
    if not fields:
        return None
    families = {f for f, _ in fields}
    if len(families) > 1:
        return MIXED
    family = families.pop()
    order = _YEAR_MONTH if family == "ym" else _DAY_TIME
    places = sorted(order.index(f) for _, f in fields)
    return family, order[places[0]], order[places[-1]]


def _literal(node: exp.Interval) -> Kind | None:
    unit = node.args.get("unit")
    if isinstance(unit, exp.IntervalSpan):
        found = [_unit(unit.this.name), _unit(unit.expression.name)]
        return _span([f for f in found if f]) if all(found) else None
    if unit is not None:
        found_unit = _unit(unit.name)
        return _span([found_unit]) if found_unit else None
    text = node.this.name if isinstance(node.this, exp.Literal) and node.this.is_string else ""
    names = _PARTS.findall(text)
    if not names or _PARTS.sub("", text).strip():
        return None
    found = [_unit(n) for n in names]
    return _span([f for f in found if f]) if all(found) else None


def _whole_literal(node: exp.Expr) -> bool:
    node = node.this if isinstance(node, exp.Neg) else node
    return isinstance(node, exp.Literal) and not node.is_string and node.this.isdigit()


def kind_of(node: Any) -> Kind | None:
    """Spark's interval type for ``node`` when the query shows it (see the module docstring), MIXED for one
    Spark stops on, else None."""
    while isinstance(node, (exp.Paren, exp.Neg)):  # a negated interval keeps its type
        node = node.this
    if isinstance(node, exp.Interval):
        return _literal(node)
    if isinstance(node, exp.MakeInterval):
        return CALENDAR
    if isinstance(node, exp.Anonymous) and node.name.upper() == "MAKE_DT_INTERVAL":
        return "dt", "DAY", "SECOND"
    if isinstance(node, (exp.Add, exp.Sub)):
        left, right = kind_of(node.this), kind_of(node.expression)
        if left is None or right is None:
            return None
        if CALENDAR in (left, right) or MIXED in (left, right):
            return MIXED if left != right else left
        return _span([(left[0], left[1]), (left[0], left[2]), (right[0], right[1]), (right[0], right[2])])
    if isinstance(node, exp.Mul):
        # A day-time interval times a whole number is DAY TO SECOND in Spark; DuckDB multiplies each of its
        # fields, so that is exact. A fraction, a year-month interval and a division round their own way.
        for interval, times in ((node.this, node.expression), (node.expression, node.this)):
            found = kind_of(interval)
            if found is not None and found[0] == "dt" and _whole_literal(times):
                return "dt", "DAY", "SECOND"
    return None


def refuse_mixed(kind: Kind | None) -> None:
    if kind == MIXED:
        raise ValueError(_MIXED)


def _two(number: str) -> str:
    """Java's %02d of a whole number at least 0: two digits or more."""
    return f"CASE WHEN {number} < 10 THEN '0' ELSE '' END || CAST({number} AS VARCHAR)"


def _seconds(micros: str) -> str:
    """BigDecimal.valueOf(micros, 6).stripTrailingZeros.toPlainString, with a 0 before one digit."""
    fraction = f"RTRIM(LPAD(CAST({micros} % 1000000 AS VARCHAR), 6, '0'), '0')"
    return (f"CASE WHEN {micros} < 10000000 THEN '0' ELSE '' END || CAST({micros} // 1000000 AS VARCHAR) || "
            f"CASE WHEN {micros} % 1000000 = 0 THEN '' ELSE '.' || {fraction} END")


def _day_time(start: str, end: str) -> str:
    """The digits of toDayTimeIntervalString for the micros kf_ivr (at least 0) from ``start`` to ``end``."""
    first = _DAY_TIME.index(start)
    last = _DAY_TIME.index(end)
    size = {"DAY": 86400000000, "HOUR": 3600000000, "MINUTE": 60000000}
    rest = "kf_ivr"
    parts = []
    for i, field in enumerate(_DAY_TIME[first:last + 1], start=first):
        if field == "SECOND":
            part = _seconds(rest)
        else:
            whole = f"{rest} // {size[field]}"
            part = f"CAST({whole} AS VARCHAR)" if field == "DAY" else _two(f"({whole})")
            # the rest, for the next field: what is left under this one
        if i > first:
            part = ("' ' || " if field == "HOUR" else "':' || ") + f"({part})"
        parts.append(f"({part})")
        if field != "SECOND":
            rest = f"({rest} % {size[field]})"
    return " || ".join(parts)


def text_sql(value: str, kind: Kind) -> str:
    """DuckDB SQL for Spark's text of the interval ``value`` (DuckDB SQL, read once) of type ``kind``. The value
    is read through TRY_CAST, so the SQL binds whatever ``value``'s type (each branch of a CASE is bound)."""
    family, start, end = kind
    iv = "kf_ivv"
    part = {name: f"CAST(DATEPART('{name}', {iv}) AS HUGEINT)" for name in ("year", "month", "day", "hour", "minute",
                                                                           "microseconds")}
    if family == "ym":
        months = f"{part['year']} * 12 + {part['month']}"
        sign = "CASE WHEN kf_ivm < 0 THEN '-' ELSE '' END"
        if start == end == "MONTH":
            digits = "CAST(kf_ivm AS VARCHAR)"
        elif start == end:
            digits = f"{sign} || CAST(ABS(kf_ivm) // 12 AS VARCHAR)"
        else:
            digits = f"{sign} || CAST(ABS(kf_ivm) // 12 AS VARCHAR) || '-' || CAST(ABS(kf_ivm) % 12 AS VARCHAR)"
        fields = start if start == end else f"{start} TO {end}"
        body = f"(LIST_TRANSFORM([{months}], lambda kf_ivm: 'INTERVAL ''' || {digits} || ''' {fields}'))[1]"
    elif family == "dt":
        micros = (f"{part['day']} * 86400000000 + {part['hour']} * 3600000000 + {part['minute']} * 60000000 + "
                  f"{part['microseconds']}")
        fields = start if start == end else f"{start} TO {end}"
        body = (f"(LIST_TRANSFORM([{micros}], lambda kf_ivm: (LIST_TRANSFORM([ABS(kf_ivm)], lambda kf_ivr: "
                f"'INTERVAL ''' || CASE WHEN kf_ivm < 0 THEN '-' ELSE '' END || {_day_time(start, end)} || "
                f"''' {fields}'))[1]))[1]")
    else:  # CalendarInterval.toString: each unit that isn't 0, and '0 seconds' for none
        rest = f"{part['microseconds']} % 60000000"
        seconds = (f"CASE WHEN {rest} = 0 THEN '' ELSE CASE WHEN {rest} < 0 THEN '-' ELSE '' END || "
                   f"CAST(ABS({rest}) // 1000000 AS VARCHAR) || CASE WHEN ABS({rest}) % 1000000 = 0 THEN '' ELSE '.' || "
                   f"RTRIM(LPAD(CAST(ABS({rest}) % 1000000 AS VARCHAR), 6, '0'), '0') END || ' seconds ' END")
        units = [(part["year"], "years"), (f"{part['month']}", "months"), (part["day"], "days"),
                 (part["hour"], "hours"), (part["minute"], "minutes")]
        written = " || ".join(f"CASE WHEN {n} = 0 THEN '' ELSE CAST({n} AS VARCHAR) || ' {u} ' END" for n, u in units)
        body = f"COALESCE(NULLIF(RTRIM({written} || {seconds}, ' '), ''), '0 seconds')"
    return (f"(LIST_TRANSFORM([TRY_CAST({value} AS INTERVAL)], lambda {iv}: CASE WHEN {iv} IS NULL THEN NULL "
            f"ELSE {body} END))[1]")


def days_text_sql(value: str) -> str:
    """Spark's text of a date minus a date, an interval of days (DuckDB's is a BIGINT of days)."""
    return (f"(LIST_TRANSFORM([TRY_CAST({value} AS BIGINT)], lambda kf_ivd: CASE WHEN kf_ivd IS NULL THEN NULL "
            f"ELSE 'INTERVAL ''' || CAST(kf_ivd AS VARCHAR) || ''' DAY' END))[1]")


_TIMES = (exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ,
          exp.DataType.Type.TIMESTAMPLTZ, exp.DataType.Type.TIMESTAMPNTZ)


def time_operand(node: Any) -> str:
    """"date" or "timestamp" for an operand of ``-`` the query shows is one, "column" for a column (its type is
    read as the query runs), else "other"."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, (exp.Cast, exp.TryCast)) and isinstance(node.to, exp.DataType) and node.to.this in _TIMES:
        return "date" if node.to.this == exp.DataType.Type.DATE else "timestamp"
    if isinstance(node, (exp.CurrentDate, exp.TsOrDsToDate, exp.StrToDate)):
        return "date"
    if isinstance(node, (exp.CurrentTimestamp, exp.StrToTime, exp.UnixToTime)):
        return "timestamp"
    if isinstance(node, exp.Column):
        return "column"
    return "other"

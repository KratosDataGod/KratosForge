"""T-SQL ``datetimeoffset``, ``AT TIME ZONE``, ``SWITCHOFFSET`` and ``TODATETIMEOFFSET`` on the local engine as a
Fabric Warehouse gives them (WR-241).

A warehouse can't keep a ``datetimeoffset`` column, but a query can use one: Microsoft's datetimeoffset page says
"In Microsoft Fabric, currently you can't create columns with the datetimeoffset data type, but you can use
datetimeoffset for converting data with the AT TIME ZONE function" (read 2026-11-18). Such a value is a local
date and time with an offset from UTC, compared and sorted by its UTC time.

Here a value is carried, while the query is translated, as two DuckDB expressions: its UTC time (a TIMESTAMP)
and its offset in minutes. What a query does with the value is written from those two:

- ``CAST`` / ``CONVERT`` to ``date``, ``time``, ``datetime2`` keep the local date and time, as T-SQL does;
  to ``varchar(n)`` / ``nvarchar(n)`` give T-SQL's text, ``yyyy-mm-dd hh:mi:ss[.nnnnnnn] {+|-}hh:mi``.
- ``=``, ``<>``, ``<``, ``>``, ``<=``, ``>=``, ``BETWEEN``, ``IS NULL`` and ``ORDER BY`` compare UTC times, so
  ``'10:00 +01:00'`` equals ``'09:00 +00:00'``.
- ``DATEPART`` and ``DATENAME`` read the local date and time; ``TZOFFSET`` the offset in minutes.
- ``SWITCHOFFSET`` keeps the UTC time with another offset; ``TODATETIMEOFFSET`` puts an offset on a datetime2.
- ``AT TIME ZONE`` takes a Windows time zone name, as a warehouse does, mapped to the IANA zone that CLDR's
  windowsZones.xml gives it. A date and time without an offset is read as local time in the zone: one in the hour
  skipped when the clocks go forward takes the offset after the change, and one in the hour that happens twice
  when they go back takes the offset before it, as Microsoft's AT TIME ZONE page says. Only the names in ZONES,
  and only times from their first year through 2037, where Windows' daylight saving rules and the local engine's
  time zone data are the same; any other time stops with a message.
- A ``datetimeoffset`` returned as a query's column is its UTC time, as a warehouse result is given in this app,
  with a note.

Anything else is refused by name: a datetimeoffset value anywhere else in a query (arithmetic, ``CASE``,
``COALESCE``, a subquery's or CTE's column, ``INSERT``), ``DATEADD`` and ``DATEDIFF`` of one, a conversion to
``datetime`` or ``smalldatetime`` or to fewer digits of a second than the value has, text that isn't written
``yyyy-mm-dd[ hh:mi[:ss[.nnnnnnn]]][ {+|-}hh:mi]``, a seventh digit of a second that isn't 0 (times are kept to
the microsecond here), ``CONVERT`` styles, and a time zone or offset that isn't written in the query.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

from sqlglot import exp
from sqlglot.errors import SqlglotError

T = exp.DataType.Type
_NOT_YET = "This T-SQL can't be run on the local engine: "

# Windows time zone names, as a warehouse takes them (sys.time_zone_info), with the IANA zone CLDR's windowsZones.xml
# maps each to (territory 001) and the first year carried. From that year through 2037, each zone's daylight saving
# rules have stayed the same law in Windows and in the IANA data: the US since 2007, the EU since 1996, Australia's
# and New Zealand's since their 2008 changes (carried from 2009), and none in the others. 2037 is the last year with
# written transitions.
ZONES: dict[str, tuple[str, int | None]] = {
    "UTC": ("UTC", None),
    "GMT Standard Time": ("Europe/London", 2007),
    "W. Europe Standard Time": ("Europe/Berlin", 2007),
    "Central Europe Standard Time": ("Europe/Budapest", 2007),
    "Romance Standard Time": ("Europe/Paris", 2007),
    "Central European Standard Time": ("Europe/Warsaw", 2007),
    "Eastern Standard Time": ("America/New_York", 2007),
    "Central Standard Time": ("America/Chicago", 2007),
    "Mountain Standard Time": ("America/Denver", 2007),
    "Pacific Standard Time": ("America/Los_Angeles", 2007),
    "US Mountain Standard Time": ("America/Phoenix", 2007),
    "Alaskan Standard Time": ("America/Anchorage", 2007),
    "Hawaiian Standard Time": ("Pacific/Honolulu", 2007),
    "Tokyo Standard Time": ("Asia/Tokyo", 2007),
    "Korea Standard Time": ("Asia/Seoul", 2007),
    "China Standard Time": ("Asia/Shanghai", 2007),
    "Singapore Standard Time": ("Asia/Singapore", 2007),
    "India Standard Time": ("Asia/Calcutta", 2007),
    "AUS Eastern Standard Time": ("Australia/Sydney", 2009),
    "New Zealand Standard Time": ("Pacific/Auckland", 2009),
}
_LAST_YEAR = 2037

NOTE = ("datetimeoffset values are given in UTC, without the offset each was written with, as a warehouse's result "
        "is given here. To keep the offset, convert the column to text in the query, such as "
        "CAST(col AS VARCHAR(34)).")
_CAN = ("Here a datetimeoffset can be cast or converted to date, time, datetime2 or varchar(34), compared, sorted, "
        "given to AT TIME ZONE, SWITCHOFFSET, DATEPART or DATENAME, or returned as a column.")
_TEXT_FORM = "yyyy-mm-dd hh:mi:ss[.nnnnnnn] {+|-}hh:mi"
_CONVERSION_FAILED = "Conversion failed when converting date and/or time from character string."
DATENAME_TZ = "KF_DATENAME_TZ"  # DATENAME(tzoffset, x), as the parser leaves it for this pass

_TIMES = {T.TIMESTAMP, T.TIMESTAMPNTZ, T.DATETIME, T.DATETIME2, T.SMALLDATETIME, T.TIMESTAMP_S, T.TIMESTAMP_MS,
          T.TIMESTAMP_NS, T.TIMESTAMPTZ, T.TIMESTAMPLTZ}
_COMPARED = (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)
_READ_LOCAL = (exp.Extract, exp.Year, exp.Month, exp.Day, exp.TimeToStr)
_NAMED: dict[type, str] = {exp.DateAdd: "DATEADD", exp.DateDiff: "DATEDIFF", exp.TimestampTrunc: "DATETRUNC",
                           exp.LastDay: "EOMONTH"}
_LITERAL = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:(T|\s+)(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,7}))?)?)?"
                      r"\s*(?:(Z)|([+-])(\d{1,2}):(\d{2}))?\s*$", re.IGNORECASE)
_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")


@dataclass
class _Dto:
    utc: exp.Expression  # the UTC date and time, a TIMESTAMP
    off: exp.Expression  # the offset in minutes
    digits: int | None  # its digits of a second, when they're known


def rewrite(tree: exp.Expression, schema: Any) -> list[str]:
    """Write each datetimeoffset value of ``tree`` in DuckDB SQL, or refuse it by name; returns notes. Run while the
    tree has its types as written (after tsql_types.check, before CONVERT's rewrite)."""
    tops = [n for n in tree.find_all(exp.Expression) if _producer(n) and not _inside_producer(n)]
    named = [n for n in tree.find_all(exp.Anonymous) if n.name.upper() == DATENAME_TZ]
    zones = [n for n in tree.find_all(exp.Extract) if n.this.name.upper() == "TIMEZONE_MINUTE"]
    if not tops and not named and not zones:
        _leftover(tree)
        return []
    if any(True for _ in tree.find_all(exp.CurrentTimestampLTZ)):
        raise ValueError(_NOT_YET + "SYSDATETIMEOFFSET isn't mapped yet. A warehouse's clock is UTC: "
                         "SYSUTCDATETIME() gives the same time without the offset.")
    kinds = _kinds(tree, schema)
    pass_ = _Pass(tree, kinds)
    for top in tops:
        if id(top) not in pass_.done:
            pass_.consume(top)
    for node in named:
        if node.parent is not None:
            pass_.datename_tz(node)
    for zone in zones:
        if zone.parent is not None:
            pass_.tzoffset(zone)
    _leftover(tree)
    return [NOTE] if pass_.returned else []


def _leftover(tree: exp.Expression) -> None:
    for kind in tree.find_all(exp.DataType):
        if kind.this == T.TIMESTAMPTZ and isinstance(kind.parent, exp.ColumnDef):  # a #temp table's
            raise ValueError(f"The data type datetimeoffset isn't supported in a Fabric Warehouse table or view (column "
                             f"{kind.parent.name}), so it's refused here as the warehouse refuses it. Use datetime2(6), "
                             "converted with AT TIME ZONE.")
        if kind.this == T.TIMESTAMPTZ:
            raise ValueError(_NOT_YET + "the data type datetimeoffset isn't mapped yet here. " + _CAN)


def _producer(node: exp.Expr | None) -> bool:
    """``node`` gives a datetimeoffset."""
    if isinstance(node, exp.Paren):
        return _producer(node.this)
    if isinstance(node, (exp.Cast, exp.TryCast)):
        to = node.args.get("to")
        return isinstance(to, exp.DataType) and to.this == T.TIMESTAMPTZ
    if isinstance(node, exp.Convert):
        return isinstance(node.this, exp.DataType) and node.this.this == T.TIMESTAMPTZ
    if isinstance(node, exp.AtTimeZone):
        return True
    return isinstance(node, exp.Anonymous) and node.name.upper() in ("SWITCHOFFSET", "TODATETIMEOFFSET")


def _inside_producer(node: exp.Expression) -> bool:
    """``node`` is the value a producer converts (it is read by ``_value`` from the outer one)."""
    parent = node.parent
    return parent is not None and _producer(parent) and (_value_arg(parent) is node or isinstance(parent, exp.Paren))


def _value_arg(node: exp.Expr) -> exp.Expr | None:
    if isinstance(node, exp.Convert):
        return node.expression
    if isinstance(node, exp.Anonymous):
        return node.expressions[0] if node.expressions else None
    return node.this


def _kinds(tree: exp.Expression, schema: Any) -> dict[int, exp.DataType | None]:
    """The type of each node of ``tree``, by a mark that survives a qualified, typed copy."""
    from sqlglot.optimizer.annotate_types import annotate_types
    from sqlglot.optimizer.qualify import qualify

    for i, node in enumerate(tree.walk()):
        node.meta["kf_offset_id"] = i
    typed = tree.copy()
    try:
        typed = qualify(typed, schema=schema, dialect="duckdb", db="dbo", expand_stars=False,
                        validate_qualify_columns=False, quote_identifiers=False, identify=False)
    except SqlglotError:
        pass  # unknown tables: literals still get their types
    try:
        typed = annotate_types(typed, schema=schema, dialect="duckdb")
    except SqlglotError:
        return {}
    return {n.meta["kf_offset_id"]: n.type for n in typed.walk() if "kf_offset_id" in n.meta}


def _fn(name: str, *args: exp.Expression) -> exp.Expression:
    return exp.Anonymous(this=name, expressions=list(args))


def _num(value: int) -> exp.Expression:
    return exp.Literal.number(value) if value >= 0 else exp.Neg(this=exp.Literal.number(-value))


def _timestamp(value: dt.datetime) -> exp.Expression:
    return exp.Cast(this=exp.Literal.string(value.isoformat(sep=" ", timespec="microseconds")),
                    to=exp.DataType.build("TIMESTAMP"))


def _null_dto(digits: int | None) -> _Dto:
    return _Dto(exp.Cast(this=exp.null(), to=exp.DataType.build("TIMESTAMP")),
                exp.Cast(this=exp.null(), to=exp.DataType.build("INT")), digits)


def _local(d: _Dto) -> exp.Expression:
    if isinstance(d.off, exp.Literal) and d.off.name == "0":
        return d.utc.copy()
    return exp.Paren(this=exp.Add(this=d.utc.copy(), expression=_fn("TO_MINUTES", d.off.copy())))


def _offset_at(zone: str, utc: exp.Expression) -> exp.Expression:
    """The offset in minutes of IANA ``zone`` at the UTC time ``utc``."""
    local = _fn("TIMEZONE", exp.Literal.string(zone), _fn("TIMEZONE", exp.Literal.string("UTC"), utc.copy()))
    gap = exp.Paren(this=exp.Sub(this=_fn("EPOCH_US", local), expression=_fn("EPOCH_US", utc.copy())))
    return exp.Paren(this=exp.IntDiv(this=gap, expression=exp.Literal.number(60000000)))


def _fail(message: str) -> exp.Expression:
    return _fn("error", exp.Literal.string(message))


def _shown(node: exp.Expr | None) -> str:
    if node is None:
        return ""
    text = node.sql(dialect="tsql")
    return text if len(text) <= 80 else text[:77] + "..."


def _literal_offset(node: exp.Expression | None, what: str) -> int:
    """Minutes of an offset written as a whole number of minutes or as '{+|-}hh:mi'."""
    value = node.this if isinstance(node, exp.Paren) else node
    minutes: int | None = None
    if isinstance(value, exp.Literal) and value.is_int:
        minutes = int(value.name)
    elif isinstance(value, exp.Neg) and isinstance(value.this, exp.Literal) and value.this.is_int:
        minutes = -int(value.this.name)
    elif isinstance(value, exp.Literal) and value.is_string:
        match = _OFFSET.fullmatch(value.name.strip())
        if not match or int(match.group(3)) > 59:
            raise ValueError(_NOT_YET + f"the offset '{value.name}' given to {what} isn't read here: write it as "
                             "'+hh:mi' or '-hh:mi', or as a whole number of minutes.")
        minutes = (int(match.group(2)) * 60 + int(match.group(3))) * (-1 if match.group(1) == "-" else 1)
    if minutes is None:
        raise ValueError(_NOT_YET + f"{what} with an offset that isn't written in the query ({_shown(node)}) isn't "
                         "mapped yet. Write the offset as '+hh:mi', '-hh:mi' or a whole number of minutes.")
    if abs(minutes) > 840:
        raise ValueError(f"The offset given to {what} is outside -14:00 to +14:00, the range T-SQL takes.")
    return minutes


def parse_literal(text: str, digits: int, safe: bool, exact: bool = True) -> _Dto:
    """A datetimeoffset(``digits``) from text, as T-SQL reads it, or a refusal for a form not read here. Only the
    offset is read when not ``exact``, so the digits of a second aren't checked."""
    match = _LITERAL.fullmatch(text)
    if not match:
        raise ValueError(_NOT_YET + f"the text '{text}' isn't read as a datetimeoffset here. Write it as "
                         f"'{_TEXT_FORM}'.")
    year, month, day, sep, hour, minute, second, fraction, zulu, sign, oh, om = match.groups()
    if zulu and sep != "T":
        raise ValueError(_NOT_YET + f"the text '{text}' isn't read as a datetimeoffset here. Write it as "
                         f"'{_TEXT_FORM}'.")
    if hour is None and (sign or zulu):
        if safe:  # a date with an offset and no time: T-SQL doesn't take it
            return _null_dto(digits)
        raise ValueError(_CONVERSION_FAILED)
    fraction = fraction or ""
    if not exact:
        fraction = fraction[:6]
    if len(fraction) == 7 and fraction[6] != "0":
        raise ValueError(_NOT_YET + f"the seventh digit of a second in '{text}' isn't carried: times are kept to "
                         "the microsecond here. Use 6 digits.")
    if fraction[digits:].strip("0"):
        raise ValueError(_NOT_YET + f"'{text}' has more digits of a second than datetimeoffset({digits}) keeps, and "
                         "Microsoft's pages don't say how they're rounded. Write it with at most "
                         f"{digits} digits.")
    offset = 0 if not sign else (int(oh) * 60 + int(om)) * (-1 if sign == "-" else 1)
    try:
        if sign and (int(om) > 59 or abs(offset) > 840):
            raise ValueError
        local = dt.datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0),
                            int((fraction + "000000")[:6]))
        utc = local - dt.timedelta(minutes=offset)
    except (ValueError, OverflowError):
        if safe:
            return _null_dto(digits)
        raise ValueError(_CONVERSION_FAILED) from None
    return _Dto(_timestamp(utc), _num(offset), digits)


def _size(kind: exp.DataType) -> int | None:
    if not kind.expressions:
        return None
    size = kind.expressions[0]
    size = size.this if isinstance(size, exp.DataTypeParam) else size
    return int(size.name) if isinstance(size, exp.Literal) and size.is_int else None


def _is_max(kind: exp.DataType) -> bool:
    return any(isinstance(p.this if isinstance(p, exp.DataTypeParam) else p, exp.Var) for p in kind.expressions)


def _offset_text(off: exp.Expression) -> exp.Expression:
    sign = exp.If(this=exp.LT(this=off.copy(), expression=exp.Literal.number(0)), true=exp.Literal.string("-"),
                  false=exp.Literal.string("+"))
    whole = _fn("ABS", off.copy())
    return _fn("PRINTF", exp.Literal.string("%s%02d:%02d"), sign,
               exp.IntDiv(this=whole, expression=exp.Literal.number(60)),
               exp.Mod(this=whole.copy(), expression=exp.Literal.number(60)))


class _Pass:
    def __init__(self, tree: exp.Expression, kinds: dict[int, exp.DataType | None]):
        self.tree = tree
        self.kinds = kinds
        self.returned = False
        self.done: set[int] = set()  # the producers already written

    # ---------- reading a value

    def kind(self, node: exp.Expression) -> exp.DataType | None:
        while isinstance(node, exp.Paren):
            node = node.this
        written = node.this if isinstance(node, exp.Convert) else node.args.get("to") \
            if isinstance(node, (exp.Cast, exp.TryCast)) else None
        if isinstance(written, exp.DataType):
            return written  # as written: CONVERT isn't typed by sqlglot
        return self.kinds.get(node.meta.get("kf_offset_id", -1))

    def naive(self, node: exp.Expression, what: str, dates: bool = False) -> tuple[exp.Expression, int | None]:
        """A date and time without an offset (datetime2, datetime, smalldatetime, or date when ``dates``), and its
        digits of a second when they're known."""
        kind = self.kind(node)
        if isinstance(node, exp.Null):
            return exp.Cast(this=exp.null(), to=exp.DataType.build("TIMESTAMP")), None
        if kind is not None and kind.this in _TIMES:
            return node.copy(), _digits(node)
        if dates and kind is not None and kind.this == T.DATE:
            return exp.Cast(this=node.copy(), to=exp.DataType.build("TIMESTAMP")), 7
        if isinstance(node, exp.Literal) and node.is_string or kind is not None and kind.this in (
                T.VARCHAR, T.NVARCHAR, T.TEXT, T.CHAR, T.NCHAR):
            raise ValueError(_NOT_YET + f"{what} of text ({_shown(node)}) isn't mapped yet. Convert it to datetime2 "
                             "or datetimeoffset first.")
        if kind is None or kind.this == T.UNKNOWN:
            raise ValueError(_NOT_YET + f"couldn't tell the type of {_shown(node)} in {what}, so it isn't run. "
                             "Convert it to datetime2 or datetimeoffset first.")
        raise ValueError(_NOT_YET + f"{what} of a {kind.sql(dialect='tsql').lower()} value ({_shown(node)}) isn't "
                         "mapped yet. T-SQL takes a datetime2, datetime, smalldatetime or datetimeoffset there.")

    def value(self, node: exp.Expression) -> _Dto:
        """The datetimeoffset that the producer ``node`` gives."""
        self.done.add(id(node))
        if isinstance(node, exp.Paren):
            return self.value(node.this)
        if isinstance(node, (exp.Cast, exp.TryCast, exp.Convert)):
            return self.cast(node)
        if isinstance(node, exp.AtTimeZone):
            return self.at_time_zone(node)
        assert isinstance(node, exp.Anonymous)
        name = node.name.upper()
        if len(node.expressions) != 2:
            raise ValueError(f"The {name.lower()} function requires 2 argument(s).")
        source, offset = node.expressions
        minutes = _literal_offset(offset, name)
        if name == "SWITCHOFFSET":
            if _producer(source):
                inner = self.value(source)
            else:  # a datetime2 is a datetimeoffset at +00:00
                value, digits = self.naive(source, name)
                inner = _Dto(value, _num(0), digits)
            return _Dto(inner.utc, _num(minutes), inner.digits)
        if _producer(source):  # TODATETIMEOFFSET of a datetimeoffset: its local date and time, as datetime2
            inner = self.value(source)
            local, digits = _local(inner), inner.digits
        else:
            local, digits = self.naive(source, name)
        utc = exp.Paren(this=exp.Sub(this=local, expression=_fn("TO_MINUTES", _num(minutes))))
        return _Dto(utc, _num(minutes), digits)

    def cast(self, node: exp.Expression) -> _Dto:
        if isinstance(node, exp.Convert):
            kind, source = node.this, node.expression
            if node.args.get("style") is not None:
                raise ValueError(_NOT_YET + "CONVERT to datetimeoffset with a style isn't mapped yet. Leave the "
                                 f"style out and write the text as '{_TEXT_FORM}'.")
            safe = bool(node.args.get("safe"))
        else:
            kind, source = node.args["to"], node.this
            safe = isinstance(node, exp.TryCast) or bool(node.args.get("safe"))
        digits = _size(kind)
        digits = 7 if digits is None else digits
        if isinstance(source, exp.Literal) and source.is_string:
            return parse_literal(source.name, digits, safe)
        if isinstance(source, exp.Null):
            return _null_dto(digits)
        if _producer(source):
            inner = self.value(source)
        else:
            value, had = self.naive(source, "CAST to datetimeoffset", dates=True)
            inner = _Dto(value, _num(0), had)
        _fewer_digits(inner.digits, digits, f"datetimeoffset({digits})")
        return _Dto(inner.utc, inner.off, digits)

    def at_time_zone(self, node: exp.AtTimeZone) -> _Dto:
        zone = node.args.get("zone")
        if not (isinstance(zone, exp.Literal) and zone.is_string):
            raise ValueError(_NOT_YET + f"AT TIME ZONE with a time zone that isn't written in the query "
                             f"({_shown(zone) if zone else ''}) isn't mapped yet. Write the zone's name.")
        if zone.name not in ZONES:
            raise ValueError(_NOT_YET + f"the time zone '{zone.name}' isn't mapped yet. AT TIME ZONE takes a "
                             "Windows time zone name, and the local engine has these: "
                             + ", ".join(f"'{z}'" for z in ZONES) + ".")
        iana, first = ZONES[zone.name]
        source = node.this
        if _producer(source):
            inner = self.value(source)
            utc = self.window(inner.utc, zone.name, first)
            if iana == "UTC":
                return _Dto(utc, _num(0), inner.digits)
            return _Dto(utc, _offset_at(iana, utc), inner.digits)
        local, digits = self.naive(source, "AT TIME ZONE")
        local = self.window(local, zone.name, first)
        if iana == "UTC":
            return _Dto(local, _num(0), digits)
        # A local time in the zone: the offset before the change for a time that happens twice or is skipped, as
        # Microsoft's page says; otherwise the one offset that gives this local time.
        before = _offset_at(iana, exp.Sub(this=local.copy(), expression=_fn("TO_HOURS", exp.Literal.number(18))))
        after = _offset_at(iana, exp.Add(this=local.copy(), expression=_fn("TO_HOURS", exp.Literal.number(18))))
        with_before = exp.Paren(this=exp.Sub(this=local.copy(), expression=_fn("TO_MINUTES", before)))
        with_after = exp.Paren(this=exp.Sub(this=local.copy(), expression=_fn("TO_MINUTES", after)))
        only_after = exp.And(this=exp.NEQ(this=_offset_at(iana, with_before), expression=before.copy()),
                             expression=exp.EQ(this=_offset_at(iana, with_after), expression=after.copy()))
        utc = exp.Case(ifs=[exp.If(this=only_after, true=with_after)], default=with_before.copy())
        return _Dto(utc, _offset_at(iana, utc), digits)

    def window(self, value: exp.Expression, zone: str, first: int | None) -> exp.Expression:
        if first is None:
            return value
        message = (_NOT_YET + f"AT TIME ZONE '{zone}' is carried here for times from {first} through {_LAST_YEAR}, "
                   "where Windows' daylight saving rules and the local engine's time zone data are the same. Run it "
                   "in a Fabric Warehouse for other times.")
        outside = exp.or_(exp.LT(this=value.copy(), expression=_timestamp(dt.datetime(first, 1, 1))),
                          exp.GTE(this=value.copy(), expression=_timestamp(dt.datetime(_LAST_YEAR + 1, 1, 1))))
        return exp.Case(ifs=[exp.If(this=outside, true=_fail(message))], default=value.copy())

    def utc_of(self, node: exp.Expression) -> exp.Expression:
        """The UTC time of what a datetimeoffset is compared with: T-SQL converts it to a datetimeoffset."""
        if _producer(node):
            return self.value(node).utc
        if isinstance(node, exp.Literal) and node.is_string:
            return parse_literal(node.name, 7, False).utc
        value, _ = self.naive(node, "a comparison with a datetimeoffset", dates=True)
        return value

    # ---------- using a value

    def consume(self, top: exp.Expression) -> None:
        node = top
        while isinstance(node.parent, exp.Paren):
            node = node.parent
        parent = node.parent
        if isinstance(parent, (exp.Cast, exp.TryCast)) and parent.this is node or \
                isinstance(parent, exp.Convert) and parent.expression is node:
            parent.replace(self.converted(parent, self.value(top)))
        elif isinstance(parent, _COMPARED):
            left, right = parent.this, parent.expression
            parent.set("this", self.utc_of(left))
            parent.set("expression", self.utc_of(right))
        elif isinstance(parent, exp.Between):
            for key in ("this", "low", "high"):
                parent.set(key, self.utc_of(parent.args[key]))
        elif isinstance(parent, exp.Extract) and parent.expression is node:
            if parent.this.name.upper() == "TIMEZONE_MINUTE":
                parent.replace(self.value(top).off)
            else:
                node.replace(_local(self.value(top)))
        elif isinstance(parent, _READ_LOCAL) and parent.this is node or isinstance(parent, exp.TsOrDsToDate) \
                and isinstance(parent.parent, (exp.Year, exp.Month, exp.Day)):
            node.replace(_local(self.value(top)))
        elif isinstance(parent, exp.Anonymous) and parent.name.upper() == DATENAME_TZ:
            parent.replace(_offset_text(self.value(top).off))
        elif isinstance(parent, exp.Is) and parent.this is node:  # IS NULL, IS NOT NULL
            node.replace(self.value(top).utc)
        elif isinstance(parent, exp.Ordered):
            node.replace(self.value(top).utc)
        elif self.returned_column(node):
            self.returned = True
            node.replace(_fn("TIMEZONE", exp.Literal.string("UTC"), self.value(top).utc))
        else:
            where = _shown(parent) if parent is not None else _shown(node)
            using = parent.parent if isinstance(parent, (exp.TimeStrToTime, exp.TsOrDsToDate)) else parent
            what = _NAMED.get(type(using))
            if what:
                raise ValueError(_NOT_YET + f"{what} of a datetimeoffset isn't mapped yet. Convert it to datetime2 "
                                 "first (its local time), or to UTC with AT TIME ZONE 'UTC'.")
            raise ValueError(_NOT_YET + f"a datetimeoffset value in {where} isn't carried yet. " + _CAN)

    def returned_column(self, node: exp.Expression) -> bool:
        holder = node.parent.parent if isinstance(node.parent, exp.Alias) else node.parent
        column = node.parent if isinstance(node.parent, exp.Alias) else node
        if holder is not self.tree or not isinstance(holder, exp.Select) or column not in holder.expressions:
            return False
        if holder.args.get("into") is not None:
            raise ValueError(f"The data type datetimeoffset isn't supported in a Fabric Warehouse table or view "
                             f"(column {column.alias_or_name or _shown(node)}), so it's refused here as the warehouse "
                             "refuses it. Use datetime2(6), converted with AT TIME ZONE.")
        return True

    def converted(self, node: exp.Expression, d: _Dto) -> exp.Expression:
        kind = node.this if isinstance(node, exp.Convert) else node.args["to"]
        if isinstance(node, exp.Convert) and node.args.get("style") is not None:
            raise ValueError(_NOT_YET + "CONVERT of a datetimeoffset with a style isn't mapped yet. Leave the style "
                             "out, or use CAST.")
        if kind.this == T.DATE:
            return exp.Cast(this=_local(d), to=kind.copy())
        if kind.this == T.TIME and (_size(kind) or 7) < 6 and (d.digits is None or d.digits > (_size(kind) or 7)):
            return _rounded_time(_local(d), _size(kind) or 0, kind)
        if kind.this in (T.DATETIME2, T.TIMESTAMP, T.TIME):
            digits = _size(kind)
            _fewer_digits(d.digits, 7 if digits is None else digits, kind.sql(dialect="tsql").lower())
            return exp.Cast(this=_local(d), to=kind.copy())
        if kind.this in (T.VARCHAR, T.NVARCHAR):
            return self.text(d, kind)
        name = kind.sql(dialect="tsql").lower()
        if kind.this in (T.DATETIME, T.SMALLDATETIME):
            raise ValueError(_NOT_YET + f"converting a datetimeoffset to {name} isn't mapped yet: Microsoft's page "
                             "doesn't say how its digits of a second are rounded there. Convert it to datetime2.")
        raise ValueError(_NOT_YET + f"converting a datetimeoffset to {name} isn't mapped yet. " + _CAN)

    def text(self, d: _Dto, kind: exp.DataType) -> exp.Expression:
        if d.digits is None:
            raise ValueError(_NOT_YET + "a datetimeoffset as text needs its digits of a second, which aren't known "
                             "here for a column. Cast it to datetimeoffset(6) first, such as "
                             "CAST(CAST(x AT TIME ZONE 'UTC' AS datetimeoffset(6)) AS varchar(33)).")
        need = 26 + (d.digits + 1 if d.digits else 0)
        size = _size(kind)
        if not _is_max(kind) and (size if size is not None else 30) < need:
            raise ValueError(_NOT_YET + f"a datetimeoffset({d.digits}) is {need} characters as text, and "
                             f"{kind.sql(dialect='tsql').lower()} is shorter. Use varchar({need}).")
        local = _local(d)
        parts: list[exp.Expression] = [_fn("STRFTIME", local, exp.Literal.string("%Y-%m-%d %H:%M:%S"))]
        if d.digits:
            micro = _fn("STRFTIME", local.copy(), exp.Literal.string("%f"))
            parts.append(_fn("SUBSTR", exp.DPipe(this=micro, expression=exp.Literal.string("0"), safe=True),
                             exp.Literal.number(1), exp.Literal.number(d.digits)))
        else:
            parts.append(exp.Literal.string(""))
        parts.append(_offset_text(d.off))
        form = "%s.%s %s" if d.digits else "%s%s %s"
        text = _fn("PRINTF", exp.Literal.string(form), *parts)
        return exp.Case(ifs=[exp.If(this=exp.Is(this=d.utc.copy(), expression=exp.null()),
                                    true=exp.Cast(this=exp.null(), to=exp.DataType.build("VARCHAR")))],
                        default=text)

    def datename_tz(self, node: exp.Anonymous) -> None:
        """DATENAME(tzoffset, x) of a value that isn't a datetimeoffset: '+00:00' for a datetime2, the offset of text
        that has one."""
        source = node.expressions[0]
        if isinstance(source, exp.Literal) and source.is_string:
            node.replace(_offset_text(parse_literal(source.name, 7, False, exact=False).off))
            return
        value, _ = self.naive(source, "DATENAME(tzoffset, ...)", dates=True)
        node.replace(exp.Case(ifs=[exp.If(this=exp.Is(this=value, expression=exp.null()),
                                          true=exp.Cast(this=exp.null(), to=exp.DataType.build("VARCHAR")))],
                              default=exp.Literal.string("+00:00")))

    def tzoffset(self, node: exp.Extract) -> None:
        """DATEPART(tzoffset, 'text with an offset'): the offset's minutes, as Microsoft's DATEPART page shows."""
        source = node.expression
        if isinstance(source, exp.Literal) and source.is_string:
            node.replace(parse_literal(source.name, 7, False, exact=False).off)


def _rounded_time(local: exp.Expression, digits: int, kind: exp.DataType) -> exp.Expression:
    """The time of day rounded to ``digits`` digits of a second, as Microsoft's datetimeoffset page shows for
    time(n) ("the value is rounded up": 12:32:10.1237 is 12:32:10.124 as time(3)). One that rounds up to the next
    day stops."""
    unit = 10 ** (6 - digits)
    day = _fn("DATE_TRUNC", exp.Literal.string("day"), local.copy())
    since = exp.Paren(this=exp.Sub(this=_fn("EPOCH_US", local.copy()), expression=_fn("EPOCH_US", day)))
    rounded = exp.Paren(this=exp.Mul(this=exp.Paren(this=exp.IntDiv(
        this=exp.Paren(this=exp.Add(this=since, expression=exp.Literal.number(unit // 2))),
        expression=exp.Literal.number(unit))), expression=exp.Literal.number(unit)))
    midnight = exp.Cast(this=exp.Literal.string("00:00:00"), to=exp.DataType.build("TIME"))
    value = exp.Cast(this=exp.Add(this=midnight, expression=_fn("TO_MICROSECONDS", rounded)), to=kind.copy())
    message = (_NOT_YET + "a datetimeoffset whose time rounds up to midnight as "
               f"{kind.sql(dialect='tsql').lower()} isn't carried yet. Use time(6).")
    return exp.Case(ifs=[exp.If(this=exp.GTE(this=rounded.copy(), expression=exp.Literal.number(86400000000)),
                                true=_fail(message))], default=value)


def _digits(node: exp.Expression) -> int | None:
    """The digits of a second of a cast's value, as T-SQL types it (None for a column, whose type here doesn't
    say)."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, (exp.Cast, exp.TryCast, exp.Convert)):
        kind = node.this if isinstance(node, exp.Convert) else node.args.get("to")
        if isinstance(kind, exp.DataType):
            if kind.this == T.DATETIME:
                return 3
            if kind.this == T.SMALLDATETIME:
                return 0
            if kind.this in (T.DATETIME2, T.TIMESTAMP):
                size = _size(kind)
                return 7 if size is None else size
    return None


def _fewer_digits(had: int | None, keep: int, name: str) -> None:
    """Converting to fewer digits of a second than the value has isn't carried: Microsoft's datetimeoffset page
    doesn't say how they're rounded. Times here have at most 6, so 6 or 7 keeps every one."""
    if keep >= 6 or had is not None and keep >= had:
        return
    raise ValueError(_NOT_YET + f"converting a datetimeoffset to {name} keeps fewer digits of a second than it "
                     f"may have, and Microsoft's datetimeoffset page doesn't say how they're rounded. Convert to "
                     f"{name.split('(')[0]}(6), or cast the value to {name.split('(')[0]}({keep}) before it gets "
                     "its offset.")


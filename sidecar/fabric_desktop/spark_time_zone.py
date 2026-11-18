"""Spark's timestamps in the session time zone on this PC, where DuckDB's own answers differ (WR-120).

A wall-clock time a daylight-saving change repeats (01:30 on the day America/Chicago falls back) is two
instants. Java, and so Spark, reads it as the earlier one (ZonedDateTime keeps the offset before the
change); DuckDB reads it as the later. So every wall clock read into a timestamp (text, a timestamp_ntz,
a date, make_timestamp, to_timestamp) takes the earlier instant: an instant an hour (or, for the zones
that move by half an hour, 30 minutes) earlier that shows the same wall clock is the one Spark gives. A
wall clock the change skips (02:30 when clocks spring forward) is moved forward by the gap in both.

date_trunc keeps the offset of the time it truncates, as Java's ZonedDateTime.truncatedTo does, so it never
gives a time after the one it was given.

Both passes slow every cast to a timestamp and every date_trunc, so they run only while a session in this
process has a zone whose offset changes (session_zone); UTC, Fabric's default, keeps DuckDB's own casts.

An interval of hours, minutes or seconds added to a timestamp is split into whole days and the rest, as
Spark's DayTimeInterval is (DateTimeUtils.timestampAddDayTime): INTERVAL 24 HOURS adds a calendar day in the
session's zone, which is 23 hours of real time on the day clocks spring forward.
"""

from __future__ import annotations

import functools
import re
from typing import Any, cast

import sqlglot
from sqlglot import exp

_T = exp.DataType.Type
_WALL_TYPES = "'TIMESTAMP', 'TIMESTAMP_S', 'TIMESTAMP_MS', 'TIMESTAMP_NS', 'DATE'"
# Text that names its own zone or offset after its time (12:00:00Z, 12:00+02:00, 12:00 America/Chicago) is an
# instant, not a wall clock.
_ZONED = r":[0-9]{2}(?:[.,][0-9]*)?\s*[A-Za-z+-]"
_DONE = "kf_tz_done"
_SECONDS = {"HOUR": 3600, "HOURS": 3600, "MINUTE": 60, "MINUTES": 60, "SECOND": 1, "SECONDS": 1}


# Spark's zone ids written as an offset (ZoneId.of, with DateTimeUtils.getZoneId's +h and UTC+h forms):
# +05:30, -8, GMT-08:00, UTC+3, UT+1, Z.
_OFFSET = re.compile(r"(?i)(?:UTC|GMT|UT)?([+-])(\d{1,2})(?::?(\d{2}))?(?::?(\d{2}))?")


def zone_id(zone: str) -> str:
    """DuckDB's name for Spark's time zone id ``zone``. An offset of whole hours is the Etc/GMT zone of
    that offset (whose sign is the other way round, as the tz database writes it); an offset with minutes
    is refused, since DuckDB has no zone for it. Any other id is DuckDB's to read (it stops on one it
    doesn't know, as Spark does)."""
    text = zone.strip()
    if text.upper() in ("Z", "UT"):
        return "UTC"
    match = _OFFSET.fullmatch(text)
    if match is None:
        return text
    sign, hours, minutes, seconds = match.groups()
    if int(minutes or 0) or int(seconds or 0):
        raise ValueError(f"The time zone {zone!r} can't be used on this PC the way it is in Fabric, because the "
                         "engine here has a zone for an offset of whole hours only, so nothing ran. Use the "
                         "region's own zone, such as Asia/Kolkata, or run the cell in Fabric.")
    if int(hours) == 0:
        return "UTC"
    if (sign == "+" and int(hours) > 14) or (sign == "-" and int(hours) > 12):
        return text  # past the offsets Etc/GMT has, and Spark's own range (18 hours) is DuckDB's to refuse
    return f"Etc/GMT{'-' if sign == '+' else '+'}{int(hours)}"


# The sessions in this process whose zone changes its offset (daylight saving, or a change of standard
# time), by id(session). Only while one is set can a wall clock be repeated or a truncation move an
# instant, so the cast and date_trunc passes below, which slow every such cast, run only then.
# Each such session's gaps: how far back (in seconds) a repeated wall clock's earlier instant can be.
_SHIFTING: dict[int, tuple[int, ...]] = {}


@functools.lru_cache(maxsize=64)
def gaps(zone: str) -> tuple[int, ...]:
    """How far back (seconds) the earlier instant of a wall clock that DuckDB zone ``zone`` repeats can be:
    () for a zone that never changes its offset from UTC, as far as this PC's tz database knows (four times a
    year from 1900 to 2100), 3600 for one that changes by whole hours, and 1800 too for one that ever moves
    by half an hour. An unknown zone counts as one that moves by either."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(zone)
    except (ValueError, OSError, KeyError):
        return (3600, 1800)
    offsets = {cast(dt.timedelta, dt.datetime(year, month, 1, tzinfo=tz).utcoffset()).total_seconds()
               for year in range(1900, 2101) for month in (1, 4, 7, 10)}
    if len(offsets) < 2:
        return ()
    return (3600,) if all((a - b) % 3600 == 0 for a in offsets for b in offsets) else (3600, 1800)


def shifts(zone: str) -> bool:
    """Whether DuckDB zone ``zone`` ever changes its offset from UTC (gaps)."""
    return bool(gaps(zone))


def session_zone(session: Any, zone: str | None) -> None:
    """Note DuckDB zone ``zone`` (None: back to UTC) as the session time zone of ``session``."""
    if zone is not None and shifts(zone):
        _SHIFTING[id(session)] = gaps(zone)
    else:
        _SHIFTING.pop(id(session), None)


def _earlier(t: str) -> str:
    """SQL for the earlier of the two instants that show the wall clock of TIMESTAMPTZ ``t``, or ``t``."""
    # t - gap shows the same wall clock as t when the zone's offset (in seconds) was gap more then: an offset
    # is read with date_part, far cheaper than writing both wall clocks out
    def offset(x: str) -> str:
        return f"date_part('timezone', {x})"
    wanted = sorted({g for zone in _SHIFTING.values() for g in zone}, reverse=True) or [3600, 1800]
    return " ".join(f"WHEN {offset(f'{t} - INTERVAL {gap} SECOND')} - {offset(t)} = {gap} THEN {t} - INTERVAL {gap} SECOND"
                    for gap in wanted)


def earlier_in(zone: str, instant: str) -> str:
    """SQL for the earlier of the two instants showing the wall clock that TIMESTAMPTZ ``instant`` shows in
    ``zone`` (both SQL), or ``instant``: for a wall clock read in a zone a function names."""
    z = f"kf_z{len(instant)}"  # a name of its own, as _depth gives the wrappers below
    whens = " ".join(f"WHEN TIMEZONE({z}.z, {z}.t - INTERVAL {gap}) = TIMEZONE({z}.z, {z}.t) "
                     f"THEN {z}.t - INTERVAL {gap}" for gap in ("1 HOUR", "30 MINUTE"))
    return f"(LIST_TRANSFORM([{{'z': {zone}, 't': {instant}}}], {z} -> CASE {whens} ELSE {z}.t END))[1]"


def _parse(sql: str) -> exp.Expression:
    return cast(exp.Expression, sqlglot.parse_one(sql, read="duckdb"))


def _put(tree: exp.Expression, name: str, node: exp.Expression) -> exp.Expression:
    for column in list(tree.find_all(exp.Column)):
        if column.name == name and not column.table:
            column.replace(node)
    return tree


def _typed(tree: exp.Expression, dtype: exp.DataType) -> None:
    """Keep the type a wrapper gives (its cast's), which sqlglot can't work out from the lambdas, for the
    typing that writes to_json from it (spark_complex)."""
    from . import spark_complex
    tree.type = dtype.copy()
    tree.meta[spark_complex.TYPED] = True


def _depth(node: exp.Expression) -> int:
    """How deep ``node``'s tree goes: a wrapper's lambda names carry it, so a wrapped node inside another
    one's operand (always shallower) never takes the outer one's names."""
    return 1 + max((_depth(c) for c in node.iter_expressions()), default=0)


def _wall_casts(statement: exp.Expression) -> None:
    for node in reversed(list(statement.find_all(exp.Cast))):  # TryCast is a Cast; the deepest first
        to = node.args.get("to")
        if not isinstance(to, exp.DataType) or to.this != _T.TIMESTAMPTZ or node.meta.get(_DONE):
            continue
        operand = node.this
        n = _depth(operand)
        w, t = f"kf_w{n}", f"kf_t{n}"
        if isinstance(operand, exp.Literal):
            if not operand.is_string or re.search(_ZONED, operand.name):
                continue  # a number of seconds, or text with its own zone, is an instant
            wall = "TRUE"
        elif isinstance(operand, exp.Cast) and isinstance(operand.args.get("to"), exp.DataType) \
                and operand.args["to"].this == _T.TIMESTAMPTZ:
            continue
        else:
            wall = (f"(typeof({w}.w) IN ({_WALL_TYPES}) OR (typeof({w}.w) = 'VARCHAR' "
                    f"AND NOT regexp_matches(CAST({w}.w AS VARCHAR), '{_ZONED}')))")
        inner = node.copy()
        inner.set("this", exp.column("w", table=w))
        inner.meta[_DONE] = True
        tree = _parse(f"(LIST_TRANSFORM([{{'w': kf_tz_operand}}], {w} -> (LIST_TRANSFORM([kf_tz_cast], {t} -> "
                      f"CASE WHEN NOT {wall} THEN {t} {_earlier(t)} ELSE {t} END))[1]))[1]")
        _put(tree, "kf_tz_cast", inner)
        _put(tree, "kf_tz_operand", operand.copy())
        _typed(tree, exp.DataType.build("TIMESTAMPTZ"))
        node.replace(tree)


def _truncs(statement: exp.Expression) -> None:
    for trunc in reversed(list(statement.find_all(exp.TimestampTrunc, exp.DateTrunc))):
        if trunc.meta.get(_DONE):
            continue
        given = trunc.this
        n = _depth(given)
        d, r = f"kf_d{n}", f"kf_r{n}"
        inner = trunc.copy()
        inner.set("this", cast(exp.Expression, exp.column("i", table=d)))
        inner.meta[_DONE] = True
        tree = _parse(f"(LIST_TRANSFORM([{{'i': kf_tz_given}}], {d} -> (LIST_TRANSFORM([kf_tz_trunc], {r} -> "
                      f"CASE WHEN typeof({r}) <> 'TIMESTAMP WITH TIME ZONE' OR NOT ({r} > {d}.i) THEN {r} "
                      f"{_earlier(r)} ELSE {r} END))[1]))[1]")
        _put(tree, "kf_tz_trunc", cast(exp.Expression, inner))
        _put(tree, "kf_tz_given", given.copy())
        if isinstance(trunc.type, exp.DataType) and trunc.type.this != _T.UNKNOWN:
            _typed(tree, trunc.type)
        trunc.replace(tree)


def _intervals(statement: exp.Expression) -> None:
    for interval in list(statement.find_all(exp.Interval)):
        unit = interval.args.get("unit")
        amount = interval.this
        sign = 1
        if isinstance(amount, exp.Neg):  # INTERVAL -25 HOURS
            amount, sign = amount.this, -1
        if not isinstance(interval.parent, (exp.Add, exp.Sub)) or not isinstance(unit, exp.Var) \
                or unit.name.upper() not in _SECONDS or not isinstance(amount, exp.Literal):
            continue
        try:
            seconds = sign * int(amount.name) * _SECONDS[unit.name.upper()]
        except ValueError:
            continue
        days = abs(seconds) // 86400 * (1 if seconds >= 0 else -1)  # toward 0, as Scala's division
        rest = seconds - days * 86400
        if days == 0:
            continue
        interval.replace(_parse(f"(INTERVAL {days} DAY + INTERVAL {rest} SECOND)"))


def fix(statement: exp.Expression) -> exp.Expression:
    """Spark's session-time-zone answers in ``statement`` (DuckDB SQL, after spark_fixes' other passes)."""
    _intervals(statement)
    if _SHIFTING:  # in UTC, or another zone that keeps its offset, no wall clock repeats
        _truncs(statement)
        _wall_casts(statement)
    return statement

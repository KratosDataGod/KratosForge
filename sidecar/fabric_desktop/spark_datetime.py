"""Spark's datetime patterns for date_format and from_unixtime, as DuckDB SQL (WR-106).

Spark 3 formats with java.time's DateTimeFormatter (Locale US) and its own rules on top
(https://spark.apache.org/docs/3.5.1/sql-ref-datetime-pattern.html). sqlglot turns a pattern into
strftime codes, which leaves letters it doesn't know as text: 'SSS' printed as SSS, 'Q' as Q,
'XXX' as XXX, and quoted text with its quotes. Here the pattern is read as Spark reads it and each
letter becomes SQL that gives Java's text; a letter Spark refuses (the week-based Y, w, u and the
rest) is refused here by name, as Spark stops with an error for it.

The value is formatted in the session's time zone, as Spark does: ``ts`` below stands for the
timestamp (with time zone) and ``local`` for its wall time in the session's zone.
"""

from __future__ import annotations

import re

# Spark: "All week-based patterns are unsupported since Spark 3.0" (DateTimeFormatterHelper).
_WEEK_BASED = set("YWwuec")
# Letters Spark refuses outright, and pattern characters java.time reserves.
_UNSUPPORTED = set("AnNpBF")
_RESERVED = set("[]{}#")
# The most letters java.time takes in a run of each.
_MOST = {"G": 5, "M": 5, "L": 5, "d": 2, "D": 3, "Q": 5, "q": 5, "E": 5, "a": 1, "h": 2, "K": 2, "k": 2,
         "H": 2, "m": 2, "s": 2, "S": 9, "O": 4, "X": 5, "x": 5, "Z": 5}
# Runs Spark itself stops on with "Too many pattern letters" (DateTimeFormatterHelper's
# unsupportedPatternLengths): five of a text letter, whose narrow form was the full form in Spark 2
# (SPARK-31771), and seven or more y (SPARK-31867).
_SPARK_MOST = {"G": 4, "M": 4, "L": 4, "E": 4, "Q": 4, "q": 4, "y": 6}

_REFUSED = ("date_format and from_unixtime can't use '{run}' in a pattern, so nothing ran: {why} "
            "See Spark's datetime patterns (sql-ref-datetime-pattern).")


# A zone id java.time's ZoneId.of reads as a fixed offset: Z, +h, +hh, +hhmm, +hh:mm, +hhmmss and
# +hh:mm:ss, alone or after UTC, GMT or UT. Spark first writes +h: as +0h: (DateTimeUtils.getZoneId).
_FIXED = re.compile(r"(?:UTC|GMT|UT)?([+-])(\d{1,2}|\d\d:?\d\d|\d\d:\d\d:\d\d|\d{6})")


def fixed_offset(zone: str) -> int | None:
    """The offset, in seconds east of UTC, of ``zone`` when Spark reads it as a fixed offset ('GMT+2',
    'UTC+05:30', '-08:00', 'Z'), else None (a region such as 'Europe/Paris', or text Java doesn't read)."""
    zone = re.sub(r"([+-])(\d):", r"\g<1>0\2:", zone, count=1)
    if zone == "Z":
        return 0
    found = _FIXED.fullmatch(zone)
    if found is None:
        return None
    digits = found.group(2).replace(":", "")
    hh, mm, ss = int(digits[:2]), int(digits[2:4] or 0), int(digits[4:6] or 0)
    seconds = hh * 3600 + mm * 60 + ss
    if mm > 59 or ss > 59 or seconds > 18 * 3600:  # ZoneOffset's range
        return None
    return -seconds if found.group(1) == "-" else seconds


def tokens(pattern: str) -> list[tuple[str, str]]:
    """``pattern`` as ('text', literal) and ('letter', run) pieces, as java.time reads it: a run of one
    letter repeated, text in single quotes ('' is a quote), and anything that isn't a letter as itself.
    Raises ValueError, naming the letters, for what Spark refuses."""
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "'":
            end = i + 1
            text = []
            while True:
                if end >= len(pattern):
                    raise ValueError(_REFUSED.format(run=pattern[i:], why="the quoted text isn't closed."))
                if pattern[end] == "'":
                    if end + 1 < len(pattern) and pattern[end + 1] == "'":
                        text.append("'")
                        end += 2
                        continue
                    break
                text.append(pattern[end])
                end += 1
            out.append(("text", "".join(text) if end > i + 1 else "'"))
            i = end + 1
            continue
        if not ("A" <= c <= "Z" or "a" <= c <= "z"):
            if c in _RESERVED:
                raise ValueError(_REFUSED.format(run=c, why="optional sections and reserved characters "
                                                 "([ ] { } #) aren't read here."))
            out.append(("text", c))
            i += 1
            continue
        end = i
        while end < len(pattern) and pattern[end] == c:
            end += 1
        run = pattern[i:end]
        _check(run)
        out.append(("letter", run))
        i = end
    return out


_ZONE_NAME = _REFUSED.format(run="{run}", why="time zone names such as EST aren't available on this PC. Use VV for "
                            "the zone's id or XXX for its offset.")


def _check(run: str) -> None:
    c, n = run[0], len(run)
    if c in _WEEK_BASED:
        raise ValueError(_REFUSED.format(run=run, why="Spark stops on it too (all week-based patterns are "
                                         "unsupported since Spark 3.0). Use extract or weekofyear instead."))
    if c in _UNSUPPORTED:
        raise ValueError(_REFUSED.format(run=run, why="Spark refuses this letter."))
    if c == "z" and n > 3:
        raise ValueError(_ZONE_NAME.format(run=run))
    if c == "V" and n != 2:
        raise ValueError(_REFUSED.format(run=run, why="the zone id is written VV."))
    if c == "O" and n not in (1, 4):
        raise ValueError(_REFUSED.format(run=run, why="the offset is written O or OOOO."))
    if c not in _MOST and c not in "yVz":
        raise ValueError(_REFUSED.format(run=run, why=f"'{c}' isn't a pattern letter."))
    if n > _SPARK_MOST.get(c, 99):
        raise ValueError(_REFUSED.format(run=run, why=f"Spark stops on it too (\"Too many pattern letters: {c}\")."))
    if n > _MOST.get(c, 99):
        raise ValueError(_REFUSED.format(run=run, why=f"too many pattern letters: {c}."))


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _pad(value: str, n: int) -> str:
    """``value`` as text with at least ``n`` digits (LPAD alone would cut a longer one)."""
    text = f"CAST({value} AS VARCHAR)"
    return text if n <= 1 else f"CASE WHEN LENGTH({text}) >= {n} THEN {text} ELSE LPAD({text}, {n}, '0') END"


# The session's offset at ts, in seconds east of UTC, and its parts.
_OFFSET = "CAST(EPOCH(local) - EPOCH(ts) AS INTEGER)"
_SIGN = f"CASE WHEN {_OFFSET} < 0 THEN '-' ELSE '+' END"
_HH = f"LPAD(CAST(ABS({_OFFSET}) // 3600 AS VARCHAR), 2, '0')"
_MM = f"LPAD(CAST(ABS({_OFFSET}) % 3600 // 60 AS VARCHAR), 2, '0')"
_SS = f"LPAD(CAST(ABS({_OFFSET}) % 60 AS VARCHAR), 2, '0')"


def _offset(colon: bool, minutes: str, z: bool) -> str:
    """ISO offset text, as java.time's X and x: minutes 'never' (+08), 'always' (+0800) or 'if any'
    (+08, or +0530); seconds when not zero; 'Z' for zero when ``z``."""
    sep = "':'" if colon else "''"
    mins = {"never": "''", "always": f"{sep} || {_MM}",
            "if any": f"CASE WHEN ABS({_OFFSET}) % 3600 = 0 THEN '' ELSE {sep} || {_MM} END"}[minutes]
    secs = f"CASE WHEN ABS({_OFFSET}) % 60 = 0 THEN '' ELSE {sep} || {_SS} END"
    text = f"{_SIGN} || {_HH} || {mins} || {secs}"
    return f"CASE WHEN {_OFFSET} = 0 THEN 'Z' ELSE {text} END" if z else f"({text})"


def _gmt(full: bool) -> str:
    """java.time's localized offset: O is GMT+8 or GMT+5:30, OOOO is GMT+08:00; GMT alone for zero."""
    if full:
        body = f"{_SIGN} || {_HH} || ':' || {_MM} || CASE WHEN ABS({_OFFSET}) % 60 = 0 THEN '' ELSE ':' || {_SS} END"
    else:
        body = (f"{_SIGN} || CAST(ABS({_OFFSET}) // 3600 AS VARCHAR) || CASE WHEN ABS({_OFFSET}) % 3600 = 0 "
                f"THEN '' ELSE ':' || {_MM} END")
    return f"CASE WHEN {_OFFSET} = 0 THEN 'GMT' ELSE 'GMT' || {body} END"


def _letter(run: str) -> str:
    c, n = run[0], len(run)
    hour = "HOUR(local)"
    if c == "G":
        return "'Anno Domini'" if n == 4 else "'AD'"
    if c == "y":
        if n == 2:
            return "STRFTIME(local, '%y')"
        if n >= 4:  # Java's EXCEEDS_PAD: a year with more digits than letters starts with + (date_format of 10000 is +10000)
            return f"CASE WHEN YEAR(local) > {10 ** n - 1} THEN '+' ELSE '' END || {_pad('YEAR(local)', n)}"
        return _pad("YEAR(local)", n) if n > 1 else "CAST(YEAR(local) AS VARCHAR)"
    if c in "ML":
        return {1: "CAST(MONTH(local) AS VARCHAR)", 2: "STRFTIME(local, '%m')", 3: "STRFTIME(local, '%b')",
                4: "STRFTIME(local, '%B')"}[n]
    if c == "d":
        return _pad("DAY(local)", n) if n > 1 else "CAST(DAY(local) AS VARCHAR)"
    if c == "D":
        return _pad("DAYOFYEAR(local)", n) if n > 1 else "CAST(DAYOFYEAR(local) AS VARCHAR)"
    if c in "Qq":
        q = "CAST(QUARTER(local) AS VARCHAR)"
        return {1: q, 2: f"LPAD({q}, 2, '0')", 3: f"'Q' || {q}",
                4: f"{q} || ['st', 'nd', 'rd', 'th'][QUARTER(local)] || ' quarter'"}[n]
    if c == "E":
        return "STRFTIME(local, '%A')" if n == 4 else "STRFTIME(local, '%a')"
    if c == "a":
        return "STRFTIME(local, '%p')"
    if c in "hKkH":
        value = {"h": f"CASE WHEN {hour} % 12 = 0 THEN 12 ELSE {hour} % 12 END", "K": f"{hour} % 12",
                 "k": f"CASE WHEN {hour} = 0 THEN 24 ELSE {hour} END", "H": hour}[c]
        return _pad(value, n) if n > 1 else f"CAST({value} AS VARCHAR)"
    if c in "ms":
        value = "MINUTE(local)" if c == "m" else "SECOND(local)"
        return _pad(value, n) if n > 1 else f"CAST({value} AS VARCHAR)"
    if c == "S":  # the first n digits of the fraction; this PC keeps microseconds, as Spark does
        return f"LEFT(STRFTIME(local, '%f') || '000', {n})"
    if c == "V":
        return "CURRENT_SETTING('TimeZone')"
    if c == "z":  # the zone's short name: UTC for UTC; any other zone's name isn't known here, so it stops
        refused = _quote(_ZONE_NAME.format(run=run))
        return f"CASE WHEN CURRENT_SETTING('TimeZone') = 'UTC' THEN 'UTC' ELSE CAST(ERROR({refused}) AS VARCHAR) END"
    if c == "O":
        return _gmt(n == 4)
    if c in "Xx":
        z = c == "X"
        return {1: _offset(False, "if any", z), 2: _offset(False, "always", z), 3: _offset(True, "always", z),
                4: _offset(False, "always", z), 5: _offset(True, "always", z)}[n]
    if c == "Z":
        if n == 4:
            return _gmt(True)
        if n == 5:
            return _offset(True, "always", True)
        return f"({_SIGN} || {_HH} || {_MM})"
    raise AssertionError(run)  # _check refused it


def format_sql(pattern: str, strict: bool = False) -> str:
    """DuckDB SQL giving Spark's text for ``pattern``, over fj_arg0 read as a timestamp in the session's
    time zone (text, a date, or a timestamp). NULL when the value is NULL or isn't a timestamp, as in Spark
    with ANSI off; ``strict`` (ANSI on) stops on text that isn't a timestamp."""
    ts = f"{'CAST' if strict else 'TRY_CAST'}(fj_arg0 AS TIMESTAMPTZ)"

    def sql(letter: str) -> str:
        return re.sub(r"\blocal\b", f"CAST({ts} AS TIMESTAMP)", re.sub(r"\bts\b", ts, _letter(letter)))
    parts = [_quote(text) if kind == "text" else sql(text) for kind, text in tokens(pattern)]
    return f"CAST(CASE WHEN {ts} IS NULL THEN NULL ELSE {' || '.join(parts) or chr(39) * 2} END AS VARCHAR)"


# ---- reading text with a pattern: to_timestamp, to_date, unix_timestamp and to_unix_timestamp

_PARSE_REFUSED = ("{fn} can't read '{run}' in a pattern on this PC, so nothing ran: {why} "
                  "See Spark's datetime patterns (sql-ref-datetime-pattern).")
_STRPTIME = {"y": "%Y", "yy": "%y", "M": "%m", "MM": "%m", "MMM": "%b", "MMMM": "%B", "d": "%d", "dd": "%d",
             "D": "%j", "DD": "%j", "DDD": "%j", "H": "%H", "HH": "%H", "h": "%I", "hh": "%I", "m": "%M",
             "mm": "%M", "s": "%S", "ss": "%S", "a": "%p", "E": "%a", "EE": "%a", "EEE": "%a", "EEEE": "%A",
             "VV": "%Z"}


def parse_format(pattern: str, fn: str) -> tuple[str, bool, bool, bool]:
    """DuckDB's strptime format for reading text with Spark's ``pattern``, whether the pattern has a
    year (without one Spark reads the date as 1970-01-01), whether it has an offset (X, x or Z, read
    with %z: the text 'Z' is read as +00:00 by the caller), and whether its year has two digits (yy:
    Spark reads 00 to 99 as 2000 to 2099, strptime 69 to 99 as 1969 to 1999). Raises ValueError naming
    what Spark refuses, or what this PC can't read."""
    try:
        pieces = tokens(pattern)
    except ValueError as exc:
        raise ValueError(str(exc).replace("date_format and from_unixtime can't use", f"{fn} can't read")) from None
    out, has_year, has_offset, two_digit = [], False, False, False
    for kind, run in pieces:
        if kind == "text":
            out.append(run.replace("%", "%%"))
            continue
        c = run[0]
        if run in _STRPTIME or c == "y" or (c == "L" and len(run) <= 4):
            code = _STRPTIME.get(run) or ("%Y" if c == "y" else _STRPTIME["M" * len(run)])
            has_year = has_year or c == "y"
            two_digit = two_digit or run == "yy"
        elif c == "S":
            code = "%f"  # one to six digits; Spark takes one up to as many as the pattern has S
        elif c in "XxZ" and len(run) <= 3:
            code, has_offset = "%z", True
        else:
            raise ValueError(_PARSE_REFUSED.format(fn=fn, run=run, why="it has no reading here. Read the text "
                                                   "with a pattern of yyyy, MM, MMM, dd, D, HH, hh, a, mm, ss, "
                                                   "S, E, VV and X letters, or run it in Fabric."))
        out.append(code)
    return "".join(out), has_year, has_offset, two_digit


def parse_sql(pattern: str, fn: str, strict: bool = False) -> str:
    """DuckDB SQL reading fj_arg0 (as text) with Spark's ``pattern``: a timestamp with time zone, the
    text's wall time in the session's zone unless it gives an offset; NULL when it doesn't match, as in
    Spark with ANSI off; ``strict`` (ANSI on) stops instead."""
    fmt, has_year, has_offset, two_digit = parse_format(pattern, fn)
    text = "CAST(fj_arg0 AS VARCHAR)"
    if has_offset:  # Spark's X reads 'Z' for +00:00; strptime's %z doesn't. Only a Z on its own, not one in a word
        text = f"REGEXP_REPLACE({text}, '(^|[^A-Za-z])Z($|[^A-Za-z])', '\\1+00:00\\2')"
    parsed = f"{'STRPTIME' if strict else 'TRY_STRPTIME'}({text}, {_quote(fmt)})"
    if not has_year:  # strptime starts from 1900-01-01
        parsed = f"({parsed} + INTERVAL 70 YEAR)"
    if two_digit:
        parsed = f"(LIST_TRANSFORM([{parsed}], kf_t -> CASE WHEN YEAR(kf_t) < 2000 THEN kf_t + INTERVAL 100 YEAR ELSE kf_t END))[1]"
    return f"CAST({parsed} AS TIMESTAMPTZ)"

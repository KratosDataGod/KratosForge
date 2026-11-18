"""T-SQL's ``FORMAT(value, format [, culture])`` on the local engine, for the cases it can give T-SQL's
answer (WR-229):

- a date or date-and-time with a .NET date format;
- an integer or decimal with the ``N``, ``F``, ``D`` and ``X`` standard formats, ``C`` (``en-US``, not below
  zero), or a custom number format such as ``'#,##0.00'``, ``'000-00-0000'`` or ``'0.0%'``;
- in the ``en-US`` culture (a warehouse's default, us_english), ``en-GB`` or ``de-DE``.

sqlglot turns the .NET format into a ``strftime`` format as it reads the query, and gets several wrong
(``'D'``, ``'ddd'``, ``'tt'``, ``'fff'``, ``'s'``), so the parser keeps the call as written
(:class:`TsqlFormat`) and this module writes it once the value's type is known. Anything else is
refused by name, never given a different answer: another culture, a culture's abbreviated names and AM/PM
(which differ between .NET's own versions), ``P``, ``E`` and ``G``, a float, and a negative number that a
format rounds to zero (``-0`` or ``0``, again by .NET version) or formats as currency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlglot import exp
from sqlglot.tokens import TokenType

NOT_YET = "This T-SQL can't be run on the local engine: "


@dataclass(frozen=True)
class Culture:
    """What FORMAT takes from a culture: its separators, its short date and time patterns, and, when not English,
    its month and day names (``None``: English, as strftime writes them)."""

    name: str
    group: str
    decimal: str
    date_sep: str
    short_date: str
    short_time: str
    long_time: str
    months: tuple[str, ...] | None = None
    days: tuple[str, ...] | None = None  # Sunday first


EN_US = Culture("en-US", ",", ".", "/", "M/d/yyyy", "h:mm tt", "h:mm:ss tt")
CULTURES = {
    "en-us": EN_US,
    "en-gb": Culture("en-GB", ",", ".", "/", "dd/MM/yyyy", "HH:mm", "HH:mm:ss"),
    "de-de": Culture("de-DE", ".", ",", ".", "dd.MM.yyyy", "HH:mm", "HH:mm:ss",
                     ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
                      "November", "Dezember"),
                     ("Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag")),
}
_CARRIED = "'en-US' (a warehouse's default), 'en-GB' and 'de-DE'"


def culture(node: exp.Expression | None) -> Culture:
    """The culture a FORMAT or PARSE names, written in the query; en-US when it names none."""
    if node is None:
        return EN_US
    if not (isinstance(node, (exp.Literal, exp.National)) and node.is_string):
        raise ValueError(NOT_YET + "the culture has to be text written in the query, such as 'de-DE'.")
    found = CULTURES.get(node.name.strip().lower())
    if found is None:
        raise ValueError(NOT_YET + f"the culture '{node.name}' isn't carried yet. The cultures carried are {_CARRIED}.")
    return found


class TsqlFormat(exp.Expression, exp.Func):
    """``FORMAT`` as written in T-SQL. If one is left in a tree, DuckDB can't run it, so it never
    silently becomes DuckDB's own ``format``."""

    arg_types = {"this": True, "format": True, "culture": False}
    _sql_names = ["TSQL_FORMAT"]


def build(args: list) -> TsqlFormat:
    if len(args) not in (2, 3):
        raise ValueError(NOT_YET + "FORMAT takes a value, a format and an optional culture.")
    return TsqlFormat(this=args[0], format=args[1], culture=args[2] if len(args) > 2 else None)


# .NET's standard date formats in en-US, as custom formats. U (universal time) is left out: it moves
# the value from the server's time zone, which a warehouse value doesn't carry.
_STANDARD_DATES = {
    "d": "M/d/yyyy",
    "D": "dddd, MMMM d, yyyy",
    "f": "dddd, MMMM d, yyyy h:mm tt",
    "F": "dddd, MMMM d, yyyy h:mm:ss tt",
    "g": "M/d/yyyy h:mm tt",
    "G": "M/d/yyyy h:mm:ss tt",
    "M": "MMMM d", "m": "MMMM d",
    "O": "yyyy'-'MM'-'dd'T'HH':'mm':'ss'.'fffffff", "o": "yyyy'-'MM'-'dd'T'HH':'mm':'ss'.'fffffff",
    "R": "ddd, dd MMM yyyy HH':'mm':'ss 'GMT'", "r": "ddd, dd MMM yyyy HH':'mm':'ss 'GMT'",
    "s": "yyyy'-'MM'-'dd'T'HH':'mm':'ss",
    "t": "h:mm tt",
    "T": "h:mm:ss tt",
    "u": "yyyy'-'MM'-'dd HH':'mm':'ss'Z'",
    "Y": "MMMM yyyy", "y": "MMMM yyyy",
}
_DATE_LETTERS = set("dfFghHKmMstyz")
_UNCARRIED = {"F": "F (fractions without trailing zeros)", "g": "g (the era)", "K": "K (the time zone)",
              "z": "z (the time zone offset)"}
_DATE_TYPES = {exp.DataType.Type.DATE, exp.DataType.Type.DATETIME, exp.DataType.Type.DATETIME2,
               exp.DataType.Type.TIMESTAMP, exp.DataType.Type.SMALLDATETIME, exp.DataType.Type.TIMESTAMP_S,
               exp.DataType.Type.TIMESTAMP_MS, exp.DataType.Type.TIMESTAMPNTZ, exp.DataType.Type.TIMESTAMP_NS}
_EXACT_NUMBERS = (exp.DataType.INTEGER_TYPES | {exp.DataType.Type.DECIMAL}) - {exp.DataType.Type.BIT}


def rewrite(node: TsqlFormat, kind: exp.DataType | None) -> None:
    """Replace ``node`` with DuckDB SQL giving T-SQL's answer for a value of type ``kind``, or raise."""
    fmt = node.args["format"]
    if not (isinstance(fmt, (exp.Literal, exp.National)) and fmt.is_string and fmt.name):
        raise ValueError(NOT_YET + "FORMAT's format has to be text written in the query, such as 'yyyy-MM-dd'.")
    try:
        where = culture(node.args.get("culture"))
    except ValueError as exc:
        raise ValueError(str(exc).replace(NOT_YET, NOT_YET + "FORMAT: ", 1)) from None
    value = node.this
    if _decimal_literal(value) or _beyond_int(value):
        kind = exp.DataType.build("DECIMAL")  # T-SQL reads 1234.5, and a whole number past int, as numeric
    if kind is not None and kind.this in _DATE_TYPES:
        body = _date(fmt.name, where)
        source = exp.Cast(this=value, to=exp.DataType.build("TIMESTAMP"))
    elif kind is not None and kind.this in _EXACT_NUMBERS:
        body = _number(fmt.name, kind, where)
        source = value
    elif kind is not None and kind.this in exp.DataType.TEXT_TYPES:
        raise ValueError(NOT_YET + "FORMAT of text is an error in T-SQL. Convert it to a date or a number first.")
    elif kind is not None and kind.this not in (exp.DataType.Type.UNKNOWN, exp.DataType.Type.NULL):
        raise ValueError(NOT_YET + f"FORMAT of a {kind.sql(dialect='tsql')} value isn't carried yet. FORMAT runs "
                         "for dates, date-and-times, integers and decimals; CAST a float or money to DECIMAL first.")
    else:
        raise ValueError(NOT_YET + "FORMAT needs to know its value's type, and here it can't be told. "
                         "CAST the value to DATETIME2, DATE or DECIMAL.")
    v = _var()
    case = exp.Case(ifs=[exp.If(this=exp.Is(this=v.copy(), expression=exp.Null()), true=exp.Null())], default=body)
    bound = exp.func("LIST_TRANSFORM", exp.Array(expressions=[source]), exp.Lambda(this=case, expressions=[v]))
    node.replace(exp.Bracket(this=bound, expressions=[exp.Literal.number(1)], offset=1))  # bound once: read once


def _beyond_int(value: exp.Expression) -> bool:
    """A whole number written past int's range is a numeric (decimal) in T-SQL, not a bigint."""
    while isinstance(value, (exp.Neg, exp.Paren)):
        value = value.this
    return isinstance(value, exp.Literal) and not value.is_string and value.name.isdigit() \
        and int(value.name) > 2_147_483_647


def _decimal_literal(value: exp.Expression) -> bool:
    """``1234.5`` is a decimal in T-SQL (``1e3`` is a float); sqlglot types both as floats."""
    while isinstance(value, (exp.Neg, exp.Paren)):
        value = value.this
    return isinstance(value, exp.Literal) and not value.is_string and "." in value.name \
        and "e" not in value.name.lower()


def _call(name: str, *args: exp.Expression) -> exp.Expression:
    """A DuckDB function by name, so later passes over sqlglot's own function nodes leave it alone."""
    return exp.Anonymous(this=name, expressions=list(args))


def _at_least(text: exp.Expression, width: int) -> exp.Expression:
    """``text`` with zeros in front up to ``width`` characters; LPAD alone would cut a longer one."""
    wide = exp.func("GREATEST", exp.Literal.number(width), _call("LENGTH", text.copy()))
    return _call("LPAD", text, exp.Cast(this=wide, to=exp.DataType.build("INTEGER")), exp.Literal.string("0"))


def _var() -> exp.Identifier:
    return exp.to_identifier("kf_value")


def _strftime(pattern: str) -> exp.Expression:
    return _call("STRFTIME", _var(), exp.Literal.string(pattern))


# The standard date formats that are the same in every culture (.NET writes them with the invariant culture).
_INVARIANT_DATES = set("OoRrsu")


def _standard_date(fmt: str, where: Culture) -> tuple[str, Culture]:
    """A one-letter .NET date format as the custom format it stands for in ``where``, and the culture to write it in."""
    if fmt in _INVARIANT_DATES:
        return _STANDARD_DATES[fmt], EN_US
    if where is EN_US:
        if fmt not in _STANDARD_DATES:
            raise ValueError(NOT_YET + f"FORMAT's date format '{fmt}' isn't carried yet.")
        return _STANDARD_DATES[fmt], EN_US
    own = {"d": where.short_date, "t": where.short_time, "T": where.long_time,
           "g": f"{where.short_date} {where.short_time}", "G": f"{where.short_date} {where.long_time}"}
    if fmt not in own:
        raise ValueError(NOT_YET + f"FORMAT's date format '{fmt}' in '{where.name}' isn't carried yet. .NET's own "
                         "versions write it differently. 'd', 'g', 'G', 't', 'T', 's', 'u', 'o' and 'R' are, and custom "
                         "formats such as 'dd MMMM yyyy'.")
    return own[fmt], where


def _names(names: tuple[str, ...], index: exp.Expression) -> exp.Expression:
    return exp.Case(ifs=[exp.If(this=exp.EQ(this=index.copy(), expression=exp.Literal.number(i)),
                                true=exp.Literal.string(n)) for i, n in enumerate(names)])


def _date(fmt: str, where: Culture = EN_US) -> exp.Expression:
    """A .NET date format as DuckDB SQL over ``kf_value`` (a TIMESTAMP), written in culture ``where``."""
    if len(fmt) == 1:
        fmt, where = _standard_date(fmt, where)
    parts: list[exp.Expression] = []
    pattern: list[str] = []  # strftime text not yet added to parts

    def flush() -> None:
        if pattern:
            parts.append(_strftime("".join(pattern)))
            pattern.clear()

    def literal(text: str) -> None:
        pattern.append(text.replace("%", "%%"))

    i = 0
    while i < len(fmt):
        c = fmt[i]
        if c in "'\"":
            end = i + 1
            text = []
            while end < len(fmt) and fmt[end] != c:
                if fmt[end] == "\\" and end + 1 < len(fmt):
                    end += 1
                text.append(fmt[end])
                end += 1
            if end >= len(fmt):
                raise ValueError(NOT_YET + f"FORMAT's date format {fmt!r} has a quote that isn't closed. Close the quote.")
            literal("".join(text))
            i = end + 1
        elif c == "\\":
            if i + 1 >= len(fmt):
                raise ValueError(NOT_YET + f"FORMAT's date format {fmt!r} ends with a backslash. Remove the backslash, or put a character after it.")
            literal(fmt[i + 1])
            i += 2
        elif c == "%":
            if i + 1 >= len(fmt) or fmt[i + 1] == "%":
                raise ValueError(NOT_YET + f"FORMAT's date format {fmt!r} has a '%' that isn't before a letter.")
            i += 1
        elif c in _DATE_LETTERS:
            n = 1
            while i + n < len(fmt) and fmt[i + n] == c:
                n += 1
            i += n
            if c in _UNCARRIED:
                raise ValueError(NOT_YET + f"FORMAT's date format {_UNCARRIED[c]} isn't carried yet.")
            if c == "f":
                if n > 7:
                    raise ValueError(NOT_YET + "FORMAT takes at most 7 f's (fractions of a second).")
                flush()  # DuckDB's %n is 9 digits of nanoseconds; .NET gives the first n, cut, not rounded
                parts.append(exp.Substring(this=_strftime("%n"), start=exp.Literal.number(1),
                                           length=exp.Literal.number(n)))
            elif c == "y":
                flush()
                year: exp.Expression = exp.Year(this=_var())
                if n <= 2:
                    year = exp.Mod(this=year, expression=exp.Literal.number(100))
                digits = exp.Cast(this=year, to=exp.DataType.build("VARCHAR"))
                parts.append(digits if n == 1 else _at_least(digits, n))
            elif where is not EN_US and (c == "t" or (c in "dM" and n == 3)):
                what = "AM/PM (t)" if c == "t" else f"abbreviated {'day' if c == 'd' else 'month'} name ({c * 3})"
                raise ValueError(NOT_YET + f"FORMAT's {what} in '{where.name}' isn't carried yet. .NET's own versions "
                                 "write it differently. The full name (dddd, MMMM) is carried.")
            elif where.months is not None and c in "dM" and n >= 4:
                flush()
                assert where.days is not None
                index = exp.Sub(this=_call("MONTH", _var()), expression=exp.Literal.number(1)) if c == "M" \
                    else _call("DAYOFWEEK", _var())  # 0 is Sunday
                parts.append(_names(where.months if c == "M" else where.days, index))
            elif c == "t":
                if n == 1:
                    flush()
                    parts.append(exp.Substring(this=_strftime("%p"), start=exp.Literal.number(1),
                                               length=exp.Literal.number(1)))
                else:
                    pattern.append("%p")
            else:
                pattern.append(_DATE_CODES[c][min(n, len(_DATE_CODES[c])) - 1])
        else:
            literal(where.date_sep if c == "/" else c)  # '/' is the culture's date separator
            i += 1
    flush()
    if not parts:
        return exp.Literal.string("")
    out = parts[0]
    for part in parts[1:]:
        out = exp.DPipe(this=out, expression=part, safe=False)
    return out


# strftime codes for 1, 2, 3, 4-or-more of a letter (a longer run takes the last one).
_DATE_CODES = {
    "d": ["%-d", "%d", "%a", "%A"],
    "M": ["%-m", "%m", "%b", "%B"],
    "h": ["%-I", "%I"],
    "H": ["%-H", "%H"],
    "m": ["%-M", "%M"],
    "s": ["%-S", "%S"],
}


_STANDARD_NUMBER = re.compile(r"([A-Za-z])(\d*)")
_BITS = {exp.DataType.Type.TINYINT: 8, exp.DataType.Type.UTINYINT: 8, exp.DataType.Type.SMALLINT: 16,
         exp.DataType.Type.INT: 32, exp.DataType.Type.BIGINT: 64}


def _number(fmt: str, kind: exp.DataType, where: Culture = EN_US) -> exp.Expression:
    """A .NET number format over ``kf_value``, an integer or decimal of type ``kind``, in culture ``where``."""
    integer = kind.this in exp.DataType.INTEGER_TYPES
    standard = _STANDARD_NUMBER.fullmatch(fmt)
    if standard is None:
        return _custom(fmt, where)
    letter, digits = standard.group(1).upper(), standard.group(2)
    if len(digits) > 2:  # .NET Framework takes at most 99, .NET Core more
        raise ValueError(NOT_YET + f"FORMAT's number format '{fmt}' isn't carried yet. A precision has at most two "
                         "digits.")
    if letter in "NF":
        return _fixed(int(digits) if digits else 2, letter == "N", where)
    if letter == "D":
        if not integer:
            raise ValueError(NOT_YET + "FORMAT's 'D' format is for integers; T-SQL doesn't give an answer for a "
                             "decimal.")
        return _whole(int(digits or "0"))
    if letter == "X":
        if kind.this not in _BITS:
            raise ValueError(NOT_YET + "FORMAT's 'X' format is for integers (tinyint, smallint, int and bigint); "
                             "T-SQL doesn't give an answer for a decimal.")
        return _hex(_BITS[kind.this], int(digits or "0"), standard.group(1) == "x")
    if letter == "C" and where is EN_US:
        return _currency(int(digits) if digits else 2)
    if letter in "CP":
        what = "'C' (currency) outside 'en-US'" if letter == "C" else "'P' (percent)"
        raise ValueError(NOT_YET + f"FORMAT's {what} isn't carried yet. .NET's own versions put the symbol and "
                         "spaces differently. A custom format such as '0.00%' or '#,##0.00 €' gives one answer.")
    raise ValueError(NOT_YET + f"FORMAT's number format '{fmt}' isn't carried yet. 'N2', 'F2', 'D5', 'X4', 'C2' "
                     "(en-US) and custom formats such as '#,##0.00' are.")


def _signed(rounded: exp.Expression, text: exp.Expression, what: str) -> exp.Expression:
    """``text`` (written from the value's size) with '-' in front when ``rounded`` is below zero. A negative value
    that rounds to zero stops the query: .NET Framework writes it as 0 and .NET Core as -0."""
    v = _var()
    below = exp.LT(this=v.copy(), expression=exp.Literal.number(0))
    zero = exp.EQ(this=rounded.copy(), expression=exp.Literal.number(0))
    gone = exp.func("ERROR", exp.Literal.string(
        NOT_YET + f"FORMAT {what} of a negative number that rounds to zero is written -0 or 0 by .NET's own "
        "versions, so it isn't carried. Round the value first."))
    return exp.Case(ifs=[exp.If(this=exp.And(this=below, expression=zero), true=gone),
                         exp.If(this=below.copy(), true=exp.DPipe(this=exp.Literal.string("-"), expression=text,
                                                                  safe=False))], default=text.copy())


def _whole(width: int) -> exp.Expression:
    """'D' with ``width`` digits at least."""
    v = _var()
    whole = exp.Cast(this=v, to=exp.DataType.build("HUGEINT"))
    sign = exp.Case(ifs=[exp.If(this=exp.LT(this=whole.copy(), expression=exp.Literal.number(0)),
                                true=exp.Literal.string("-"))], default=exp.Literal.string(""))
    text = exp.Cast(this=exp.Abs(this=whole.copy()), to=exp.DataType.build("VARCHAR"))
    return exp.DPipe(this=sign, expression=_at_least(text, width) if width else text, safe=False)


def _hex(bits: int, width: int, lower: bool) -> exp.Expression:
    """'X': the value's bits as hexadecimal, so a negative int is its two's complement (-1 is FFFFFFFF)."""
    span = exp.Literal.number(2 ** bits)
    wrapped = exp.Mod(this=exp.paren(exp.Add(this=exp.Cast(this=_var(), to=exp.DataType.build("HUGEINT")),
                                             expression=span), copy=False), expression=span.copy())
    text = _call("FORMAT", exp.Literal.string("{:x}" if lower else "{:X}"),
                 exp.Cast(this=exp.paren(wrapped, copy=False), to=exp.DataType(this=exp.DataType.Type.UBIGINT)))
    return _at_least(text, width) if width else text


def _rounded(places: int) -> exp.Expression:
    if places > 38:
        raise ValueError(NOT_YET + "FORMAT's number formats are carried with at most 38 decimal places.")
    return exp.Round(this=exp.Abs(this=_var()), decimals=exp.Literal.number(places))


def _digits(rounded: exp.Expression) -> tuple[exp.Expression, exp.Expression]:
    """The whole part (a HUGEINT) and the decimals as text, of a value rounded in its own type: a cast to
    DECIMAL(38, places) would leave no room for the whole part when places is high (#2691)."""
    whole = exp.Cast(this=exp.Trunc(this=rounded.copy()), to=exp.DataType.build("HUGEINT"))
    decimals = _call("SPLIT_PART", exp.Cast(this=rounded.copy(), to=exp.DataType.build("VARCHAR")),
                     exp.Literal.string("."), exp.Literal.number(2))
    return whole, decimals


def _grouped(whole: exp.Expression, where: Culture) -> exp.Expression:
    text = _call("FORMAT", exp.Literal.string("{:,}"), whole)
    return text if where.group == "," else _call("REPLACE", text, exp.Literal.string(","),
                                            exp.Literal.string(where.group))


def _fixed(places: int, grouped: bool, where: Culture) -> exp.Expression:
    """'N' and 'F' with ``places`` decimals, rounded half away from zero as .NET rounds a decimal, with the
    culture's separator between thousands for 'N' and its decimal separator. A negative value that rounds to zero
    stops the query, as in a custom format: .NET Framework writes 0.00 and .NET Core -0.00."""
    rounded = _rounded(places)
    whole, decimals = _digits(rounded)
    out: exp.Expression = _grouped(whole, where) if grouped else exp.Cast(this=whole, to=exp.DataType.build("VARCHAR"))
    if places:
        fraction = _call("RPAD", decimals, exp.Literal.number(places), exp.Literal.string("0"))
        out = exp.DPipe(this=exp.DPipe(this=out, expression=exp.Literal.string(where.decimal), safe=False),
                        expression=fraction, safe=False)
    return _signed(rounded, out, f"'{'N' if grouped else 'F'}{places}'")


def _currency(places: int) -> exp.Expression:
    """'C' in en-US: $1,234.50. Below zero .NET Framework writes ($1,234.50) and .NET Core -$1,234.50, so a
    negative value stops the query by name."""
    below = exp.LT(this=_var(), expression=exp.Literal.number(0))
    stop = exp.func("ERROR", exp.Literal.string(
        NOT_YET + "FORMAT 'C' of a negative number is written ($1.00) or -$1.00 by .NET's own versions, so it isn't "
        "carried. A custom format such as '$#,##0.00;-$#,##0.00' gives one answer."))
    text = exp.DPipe(this=exp.Literal.string("$"), expression=_fixed(places, True, EN_US), safe=False)
    return exp.Case(ifs=[exp.If(this=below, true=stop)], default=text)


# ---- custom number formats: '#,##0.00', '000-00-0000', '0.0%', '$#,##0;($#,##0)'

def _tokens(section: str) -> list[tuple[str, str]]:
    """A custom format's section as ('0' | '#' | '.' | ',' | '%' | 'text', text) pairs."""
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(section):
        c = section[i]
        if c in "'\"":
            end = section.find(c, i + 1)
            if end < 0:
                raise ValueError(NOT_YET + f"FORMAT's number format {section!r} has a quote that isn't closed. Close the quote.")
            out.append(("text", section[i + 1:end]))
            i = end + 1
            continue
        if c == "\\":
            if i + 1 >= len(section):
                raise ValueError(NOT_YET + f"FORMAT's number format {section!r} ends with a backslash. Remove the backslash, or put a character after it.")
            out.append(("text", section[i + 1]))
            i += 2
            continue
        if c in "Ee" and i + 1 < len(section) and section[i + 1] in "0+-":
            raise ValueError(NOT_YET + "FORMAT's scientific notation (E0, e+0) in a custom format isn't carried yet.")
        if c == "‰":
            raise ValueError(NOT_YET + "FORMAT's per mille sign (‰) isn't carried yet.")
        out.append((c, c) if c in "0#.,%" else ("text", c))
        i += 1
    return out


@dataclass
class _Section:
    before: list[tuple[str, str]]
    whole: list[tuple[str, str]]  # placeholders, ',' between them, and text between them
    places: str  # '0' and '#' after the point, as written
    point: bool
    after: list[tuple[str, str]]
    percent: int


def _section(text: str) -> _Section:
    tokens = _tokens(text)
    marks = [i for i, (k, _) in enumerate(tokens) if k in "0#."]
    if not marks:
        raise ValueError(NOT_YET + f"FORMAT's number format {text!r} isn't carried yet. It has no 0 or # for the "
                         "number. Write the text in quotes next to one.")
    first, last = marks[0], marks[-1]
    inside = tokens[first:last + 1]
    point = next((i for i, (k, _) in enumerate(inside) if k == "."), None)
    whole = inside if point is None else inside[:point]
    places = [] if point is None else inside[point + 1:]
    if any(k not in "0#" for k, _ in places):
        raise ValueError(NOT_YET + f"FORMAT's number format {text!r} has something other than 0 or # after the "
                         "point, which isn't carried yet.")
    if whole and whole[-1][0] == ",":
        raise ValueError(NOT_YET + f"FORMAT's number format {text!r} has a ',' right before the point or the end, "
                         "which divides by 1,000 and isn't carried yet.")
    outside = tokens[:first] + tokens[last + 1:]
    if any(k in ",." for k, _ in outside):
        raise ValueError(NOT_YET + f"FORMAT's number format {text!r} has a ',' or '.' away from the digits, which "
                         "isn't carried yet.")
    percent = sum(1 for k, _ in tokens if k == "%")
    if percent > 1:
        raise ValueError(NOT_YET + "FORMAT's number format with more than one % isn't carried yet.")
    if any(k == "%" for k, _ in inside):
        raise ValueError(NOT_YET + f"FORMAT's number format {text!r} has a % among the digits, which isn't carried "
                         "yet. Put it before or after them.")
    return _Section(tokens[:first], whole, "".join(k for k, _ in places), point is not None, tokens[last + 1:], percent)


def _custom(fmt: str, where: Culture) -> exp.Expression:
    """A custom .NET number format, of one section, or two (the second for numbers below zero, written without
    their '-')."""
    sections, quote, start = [], "", 0
    for i, c in enumerate(fmt):
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == ";" and (i == 0 or fmt[i - 1] != "\\"):
            sections.append(fmt[start:i])
            start = i + 1
    sections.append(fmt[start:])
    if len(sections) > 2:
        raise ValueError(NOT_YET + "FORMAT's custom number format with a third section (for zero) isn't carried yet.")
    first = _section(sections[0])
    if len(sections) == 1:
        rounded, text = _written(first, where)
        return _signed(rounded, text, f"'{fmt}'")
    rounded, text = _written(first, where)
    neg_rounded, neg_text = _written(_section(sections[1]), where)
    v = _var()
    below = exp.LT(this=v.copy(), expression=exp.Literal.number(0))
    gone = exp.func("ERROR", exp.Literal.string(
        NOT_YET + f"FORMAT '{fmt}' of a negative number that rounds to zero isn't carried yet. .NET's own versions "
        "choose the section differently. Round the value first."))
    zero = exp.EQ(this=neg_rounded, expression=exp.Literal.number(0))
    del rounded
    return exp.Case(ifs=[exp.If(this=exp.And(this=below, expression=zero), true=gone),
                         exp.If(this=below.copy(), true=neg_text)], default=text)


def _written(section: _Section, where: Culture) -> tuple[exp.Expression, exp.Expression]:
    """The section written over ``kf_value``'s size (no sign), and the rounded size."""
    places = len(section.places)
    least = section.places.rfind("0") + 1  # decimals always written: up to the last 0
    size: exp.Expression = exp.Abs(this=_var())
    if section.percent:
        size = exp.Mul(this=size, expression=exp.Literal.number(100))
    if places > 38:
        raise ValueError(NOT_YET + "FORMAT's number formats are carried with at most 38 decimal places.")
    rounded = exp.Round(this=size, decimals=exp.Literal.number(places))
    whole, decimals = _digits(rounded)
    marks = [k for k, _ in section.whole if k in "0#"]
    zeros = len(marks) - marks.index("0") if "0" in marks else 0  # from the first 0 on, every digit is written
    grouped = any(k == "," for k, _ in section.whole)
    texts = any(k == "text" for k, _ in section.whole)
    digits = exp.Cast(this=whole.copy(), to=exp.DataType.build("VARCHAR"))
    if texts:
        if grouped or "#" in marks:
            raise ValueError(NOT_YET + "FORMAT's custom number format with text among the digits is carried with "
                             "0s only, such as '000-00-0000'.")
        int_text = _filled(digits, section.whole)
    elif grouped:
        if zeros > 1:
            raise ValueError(NOT_YET + "FORMAT's custom number format with ',' and more than one 0 before the point "
                             "isn't carried yet. '#,##0' is.")
        int_text = _grouped(whole, where)
        if not zeros:
            int_text = exp.Case(ifs=[exp.If(this=exp.EQ(this=whole.copy(), expression=exp.Literal.number(0)),
                                            true=exp.Literal.string(""))], default=int_text)
    elif zeros:
        int_text = _at_least(digits, zeros)
    else:  # '#' or '.00': a whole part of 0 is left out
        int_text = exp.Case(ifs=[exp.If(this=exp.EQ(this=whole.copy(), expression=exp.Literal.number(0)),
                                        true=exp.Literal.string(""))], default=digits)
    out = int_text
    if places:
        fraction: exp.Expression = _call("RPAD", decimals, exp.Literal.number(places), exp.Literal.string("0"))
        if least < places:  # decimals past the last 0 are written only when they aren't zeros at the end
            kept = _call("RTRIM", exp.Substring(this=fraction.copy(), start=exp.Literal.number(least + 1)),
                         exp.Literal.string("0"))
            fraction = exp.DPipe(this=_call("LEFT", fraction, exp.Literal.number(least)), expression=kept, safe=False) \
                if least else kept
        point = exp.Case(ifs=[exp.If(this=exp.EQ(this=fraction.copy(), expression=exp.Literal.string("")),
                                     true=exp.Literal.string(""))],
                         default=exp.DPipe(this=exp.Literal.string(where.decimal), expression=fraction, safe=False))
        out = exp.DPipe(this=out, expression=point, safe=False)
    before = "".join(t for _, t in section.before)
    after = "".join(t for _, t in section.after)
    if before:
        out = exp.DPipe(this=exp.Literal.string(before), expression=out, safe=False)
    if after:
        out = exp.DPipe(this=out, expression=exp.Literal.string(after), safe=False)
    return rounded, out


def _filled(digits: exp.Expression, whole: list[tuple[str, str]]) -> exp.Expression:
    """'000-00-0000': the digits fill the 0s from the right, with the text between them; digits past the 0s go in
    front."""
    n = sum(1 for k, _ in whole if k == "0")
    padded = _at_least(digits, n)
    extra = _call("LEFT", padded.copy(), exp.Cast(this=exp.func("GREATEST", exp.Literal.number(0), exp.Sub(
        this=_call("LENGTH", padded.copy()), expression=exp.Literal.number(n))), to=exp.DataType.build("INTEGER")))
    last = _call("RIGHT", padded, exp.Literal.number(n))
    out: exp.Expression = extra
    k = 0
    for kind, text in whole:
        if kind == "0":
            k += 1
            piece: exp.Expression = exp.Substring(this=last.copy(), start=exp.Literal.number(k),
                                                  length=exp.Literal.number(1))
        else:
            piece = exp.Literal.string(text)
        out = exp.DPipe(this=out, expression=piece, safe=False)
    return out


# ---- PARSE and TRY_PARSE (WR-229)

class TsqlParse(exp.Expression, exp.Func):
    """``PARSE(text AS type [USING culture])`` or ``TRY_PARSE(...)`` as written; :func:`parses` writes it."""

    arg_types = {"this": True, "to": True, "culture": False, "safe": False}
    _sql_names = ["TSQL_PARSE"]


def parser(safe: bool) -> Any:
    """The parser function for PARSE (``safe`` False) or TRY_PARSE: ``x AS type`` and an optional ``USING culture``."""

    def parse(self: Any) -> TsqlParse:
        this = self._parse_bitwise()
        if not self._match(TokenType.ALIAS):
            self.raise_error(f"Expected AS in {'TRY_PARSE' if safe else 'PARSE'}")
        to = self._parse_types()
        where = self._parse_bitwise() if self._match_text_seq("USING") else None
        return TsqlParse(this=this, to=to, culture=where, safe=safe)

    return parse


_PARSED_NUMBERS = exp.DataType.INTEGER_TYPES - {exp.DataType.Type.BIT} | {
    exp.DataType.Type.DECIMAL, exp.DataType.Type.DOUBLE, exp.DataType.Type.FLOAT}
_TSQL_NAMES = {exp.DataType.Type.INT: "int", exp.DataType.Type.BIGINT: "bigint", exp.DataType.Type.SMALLINT: "smallint",
               exp.DataType.Type.TINYINT: "tinyint", exp.DataType.Type.UTINYINT: "tinyint",
               exp.DataType.Type.DOUBLE: "float", exp.DataType.Type.FLOAT: "real"}
_PARSED_DATES = _DATE_TYPES | {exp.DataType.Type.TIME, exp.DataType.Type.TIMESTAMPTZ}


def parses(tree: exp.Expression) -> None:
    """Write each PARSE and TRY_PARSE in ``tree``: text read as a number in the culture, or refused by name."""
    for node in list(tree.find_all(TsqlParse)):
        node.replace(_parse(node))


def _parse(node: TsqlParse) -> exp.Expression:
    """Text read as a number of type ``to`` in the culture, as .NET reads it. Text that is a sign, digits, the
    culture's thousands separators and one decimal separator (and spaces around) gives the number. Text with no
    digit fails: PARSE stops with T-SQL's error and TRY_PARSE gives NULL. Anything else (a currency sign, an
    exponent, parentheses, a sign at the end) .NET may read, so it stops by name rather than give another answer."""
    name = "TRY_PARSE" if node.args.get("safe") else "PARSE"
    to = node.args["to"]
    if not isinstance(to, exp.DataType):
        raise ValueError(NOT_YET + f"{name} needs a type after AS, such as INT or DECIMAL(10, 2).")
    try:
        where = culture(node.args.get("culture"))
    except ValueError as exc:
        raise ValueError(str(exc).replace(NOT_YET, NOT_YET + f"{name}: ", 1)) from None
    if to.this in _PARSED_DATES:
        raise ValueError(NOT_YET + f"{name} to a date or time isn't carried yet. .NET reads dates in many ways. Use "
                         "CONVERT with a style, such as CONVERT(DATE, x, 103) for dd/mm/yyyy.")
    if to.this in (exp.DataType.Type.MONEY, exp.DataType.Type.SMALLMONEY):
        raise ValueError(NOT_YET + f"{name} to MONEY or SMALLMONEY isn't carried yet, as MONEY and SMALLMONEY "
                         "aren't. Use DECIMAL(19, 4).")
    if to.this not in _PARSED_NUMBERS:
        raise ValueError(NOT_YET + f"{name} converts text to a number or a date in T-SQL; {to.sql(dialect='tsql')} "
                         "isn't one. Use CAST.")
    integer = to.this in exp.DataType.INTEGER_TYPES
    g, d = re.escape(where.group), re.escape(where.decimal)
    whole = rf"[0-9]+(?:{g}[0-9]{{3}})*" if not integer else rf"[0-9]+(?:{g}[0-9]{{3}})*"
    shape = rf"^[+-]?(?:{whole}(?:{d}[0-9]*)?|{d}[0-9]+)$" if not integer else rf"^[+-]?{whole}$"
    text = _call("REGEXP_REPLACE", exp.Cast(this=node.this, to=exp.DataType.build("VARCHAR")),
                 exp.Literal.string(r"^\s+|\s+$"), exp.Literal.string(""), exp.Literal.string("g"))
    v = _var()
    plain: exp.Expr = exp.func("REPLACE", exp.Cast(this=v.copy(), to=exp.DataType.build("VARCHAR")),
                                     exp.Literal.string(where.group), exp.Literal.string(""))
    if where.decimal != ".":
        plain = exp.func("REPLACE", plain, exp.Literal.string(where.decimal), exp.Literal.string("."))
    number = exp.TryCast(this=plain, to=to.copy()) if node.args.get("safe") else exp.Cast(this=plain, to=to.copy())
    number.meta["kf_digits"] = True  # digits only: converts the same in T-SQL and DuckDB, so no type note
    fits = _call("REGEXP_MATCHES", v.copy(), exp.Literal.string(shape))
    no_digit: exp.Expr = exp.Not(this=_call("REGEXP_MATCHES", v.copy(), exp.Literal.string("[0-9]")))
    if to.this in (exp.DataType.Type.DOUBLE, exp.DataType.Type.FLOAT):  # Infinity, NaN and ∞ are numbers to .NET
        no_digit = exp.And(this=no_digit, expression=exp.Not(this=_call(
            "REGEXP_MATCHES", v.copy(), exp.Literal.string("(?i)inf|nan|∞"))))
    kind = _TSQL_NAMES.get(to.this) or to.sql(dialect="tsql").lower()
    failed: exp.Expr = exp.Null() if node.args.get("safe") else exp.func("ERROR", exp.DPipe(
        this=exp.DPipe(this=exp.Literal.string("Error converting string value '"), expression=v.copy(), safe=False),
        expression=exp.Literal.string(f"' into data type {kind} using culture '{where.name}'."), safe=False))
    unsure = exp.func("ERROR", exp.DPipe(this=exp.DPipe(
        this=exp.Literal.string(NOT_YET + f"{name} of '"), expression=v.copy(), safe=False),
        expression=exp.Literal.string(
            f"' isn't carried yet. Here {name} reads a sign, digits, '{where.group}' between thousands"
            + ("" if integer else f" and '{where.decimal}' before decimals")
            + ". .NET may read the rest another way."), safe=False))
    case = exp.Case(ifs=[exp.If(this=exp.Is(this=v.copy(), expression=exp.Null()), true=exp.Null()),
                         exp.If(this=fits, true=number),
                         exp.If(this=no_digit, true=failed)], default=unsure)
    bound = exp.func("LIST_TRANSFORM", exp.Array(expressions=[text]), exp.Lambda(this=case, expressions=[v]))
    return exp.Bracket(this=bound, expressions=[exp.Literal.number(1)], offset=1)

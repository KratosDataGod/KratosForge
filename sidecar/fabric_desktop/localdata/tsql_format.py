"""T-SQL's ``FORMAT(value, format [, culture])`` on the local engine, for the cases it can give T-SQL's
answer: a date or date-and-time with a .NET date format, and an integer or decimal with the ``N``,
``F`` or ``D`` number formats, in the ``en-US`` culture (a warehouse's default, us_english).

sqlglot turns the .NET format into a ``strftime`` format as it reads the query, and gets several wrong
(``'D'``, ``'ddd'``, ``'tt'``, ``'fff'``, ``'s'``), so the parser keeps the call as written
(:class:`TsqlFormat`) and this module writes it once the value's type is known. Anything else is
refused by name, never given a different answer.
"""

from __future__ import annotations

from sqlglot import exp

NOT_YET = "This T-SQL can't be run on the local engine: "


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
               exp.DataType.Type.TIMESTAMP_MS}
_EXACT_NUMBERS = (exp.DataType.INTEGER_TYPES | {exp.DataType.Type.DECIMAL}) - {exp.DataType.Type.BIT}


def rewrite(node: TsqlFormat, kind: exp.DataType | None) -> None:
    """Replace ``node`` with DuckDB SQL giving T-SQL's answer for a value of type ``kind``, or raise."""
    fmt, culture = node.args["format"], node.args.get("culture")
    if not (isinstance(fmt, exp.Literal) and fmt.is_string and fmt.name):
        raise ValueError(NOT_YET + "FORMAT's format has to be text written in the query, such as 'yyyy-MM-dd'.")
    if culture is not None and not (isinstance(culture, exp.Literal) and culture.is_string
                                    and culture.name.lower() == "en-us"):
        raise ValueError(NOT_YET + "FORMAT is carried only for the 'en-US' culture, a warehouse's default.")
    value = node.this
    if _decimal_literal(value):
        kind = exp.DataType.build("DECIMAL")
    if kind is not None and kind.this in _DATE_TYPES:
        body = _date(fmt.name)
        source = exp.Cast(this=value, to=exp.DataType.build("TIMESTAMP"))
    elif kind is not None and kind.this in _EXACT_NUMBERS:
        body = _number(fmt.name, kind.this in exp.DataType.INTEGER_TYPES)
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


def _date(fmt: str) -> exp.Expression:
    """A .NET date format as DuckDB SQL over ``kf_value`` (a TIMESTAMP)."""
    if len(fmt) == 1:
        if fmt not in _STANDARD_DATES:
            raise ValueError(NOT_YET + f"FORMAT's date format '{fmt}' isn't carried yet.")
        fmt = _STANDARD_DATES[fmt]
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
                raise ValueError(NOT_YET + f"FORMAT's date format {fmt!r} has a quote that isn't closed.")
            literal("".join(text))
            i = end + 1
        elif c == "\\":
            if i + 1 >= len(fmt):
                raise ValueError(NOT_YET + f"FORMAT's date format {fmt!r} ends with a backslash.")
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
            literal(c)
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


def _number(fmt: str, integer: bool) -> exp.Expression:
    """``N``, ``F`` and ``D`` (integers only), with an optional precision, over ``kf_value``: rounded
    half away from zero, as .NET rounds a decimal, with ``,`` between thousands for ``N``."""
    letter, digits = fmt[0].upper(), fmt[1:]
    if letter not in "NFD" or not (digits == "" or (digits.isdigit() and len(digits) <= 2)):
        raise ValueError(NOT_YET + f"FORMAT's number format '{fmt}' isn't carried yet. 'N2', 'F2' and 'D5' "
                         "(and their other precisions) are.")
    v = _var()
    if letter == "D":
        if not integer:
            raise ValueError(NOT_YET + "FORMAT's 'D' format is for integers; T-SQL doesn't give an answer for a "
                             "decimal.")
        whole = exp.Cast(this=v, to=exp.DataType.build("HUGEINT"))
        sign = exp.Case(ifs=[exp.If(this=exp.LT(this=whole.copy(), expression=exp.Literal.number(0)),
                                    true=exp.Literal.string("-"))], default=exp.Literal.string(""))
        text = exp.Cast(this=exp.Abs(this=whole.copy()), to=exp.DataType.build("VARCHAR"))
        width = int(digits or "0")
        return exp.DPipe(this=sign, expression=_at_least(text, width) if width else text, safe=False)
    places = int(digits) if digits else 2
    if places > 38:
        raise ValueError(NOT_YET + "FORMAT's number formats are carried with at most 38 decimal places.")
    rounded = exp.Cast(this=exp.Round(this=v, decimals=exp.Literal.number(places)),
                       to=exp.DataType.build(f"DECIMAL(38, {places})"))
    if letter == "F":
        return exp.Cast(this=rounded, to=exp.DataType.build("VARCHAR"))
    sign = exp.Case(ifs=[exp.If(this=exp.LT(this=rounded.copy(), expression=exp.Literal.number(0)),
                                true=exp.Literal.string("-"))], default=exp.Literal.string(""))
    grouped = _call("FORMAT", exp.Literal.string("{:,}"),
                    exp.Cast(this=exp.Trunc(this=exp.Abs(this=rounded.copy())), to=exp.DataType.build("HUGEINT")))
    out: exp.Expression = exp.DPipe(this=sign, expression=grouped, safe=False)
    if places:
        fraction = _call("SPLIT_PART", exp.Cast(this=rounded.copy(), to=exp.DataType.build("VARCHAR")),
                            exp.Literal.string("."), exp.Literal.number(2))
        out = exp.DPipe(this=exp.DPipe(this=out, expression=exp.Literal.string("."), safe=False),
                        expression=fraction, safe=False)
    return out

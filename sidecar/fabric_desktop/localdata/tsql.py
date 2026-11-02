"""T-SQL on the local engine (FD-LDE-004): a T-SQL script read with sqlglot and run as DuckDB SQL,
with the differences that change answers translated, and anything that can't be carried refused.

sqlglot already turns most T-SQL into DuckDB SQL (TOP, brackets, ISNULL, IIF, DATEADD, CROSS
APPLY, SELECT INTO, OFFSET/FETCH). On top of that this module:

- divides integers as integers (``7 / 2`` is 3), and averages integers as integers, as T-SQL
  does, using the project's column types to tell. A division whose types it can't tell is left
  as DuckDB divides and reported in ``notes``;
- reads date parts by T-SQL's names (``y`` is day of year, ``w`` weekday, ``m`` month), counts
  ``DATEDIFF(week, ...)`` in Sundays crossed, starts ``DATETRUNC(week, ...)`` on Sunday, and keeps a
  date a date in ``DATEADD`` and ``DATETRUNC``;
- counts ``LEN`` without trailing spaces, numbers ``DATEPART(weekday)`` from Sunday = 1 and
  ``DATEPART(week)`` from the week of 1 January (Fabric's defaults: DATEFIRST 7, us_english);
- joins strings with ``+`` as T-SQL does when a side is known to be text;
- runs ``BIT`` as a boolean, and ``CHOOSE``, ``ROUND(x, n, 1)``, ``DATALENGTH``, ``STRING_SPLIT`` and
  ``TOP n WITH TIES``, which DuckDB doesn't have (``ISNUMERIC`` is refused for now);
- gives ``POWER`` its first argument's type (an integer base gives an integer; a decimal base is refused);
- matches ``LIKE`` patterns with ``[a-c]`` and ``[^a-c]`` character ranges, as a regular expression;
- keeps ``#temp`` tables apart from ordinary tables of the same name (as temporary ``"#name"``);
- returns ``GETDATE()`` and friends as UTC times without a zone, as a Fabric Warehouse does.

It refuses, rather than half-translates: variables, control flow and batches (``DECLARE``,
``SET @x``, ``IF``, ``WHILE``, ``BEGIN ... END``, ``EXEC``, ``PRINT``), transactions (a Delta write
isn't part of one here), procedures and functions, ``USE``, and anything sqlglot reports it can't
write in DuckDB SQL (``FOR JSON``, ``OPTION (...)``). ``GO`` lines split the script.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import sqlglot
from sqlglot import ErrorLevel, exp
from sqlglot.dialects.tsql import TSQL
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

from ..sql import KfDuckDB

_GO = re.compile(r"^[ \t]*GO[ \t]*(?:--[^\n]*)?$", re.IGNORECASE | re.MULTILINE)
_INTEGERS = exp.DataType.INTEGER_TYPES
_TEXT = exp.DataType.TEXT_TYPES
_NUMBERS = exp.DataType.NUMERIC_TYPES
_DROPPED_SETTINGS = {"NOCOUNT", "ANSI_NULLS", "QUOTED_IDENTIFIER", "ANSI_WARNINGS", "ANSI_PADDING", "XACT_ABORT"}

_REFUSED = {
    exp.Declare: "variables (DECLARE)",
    exp.IfBlock: "IF blocks",
    exp.WhileBlock: "WHILE loops",
    exp.Execute: "EXEC",
    exp.Use: "USE",
    exp.Transaction: "transactions (BEGIN TRAN)",
    exp.Commit: "transactions (COMMIT)",
    exp.Rollback: "transactions (ROLLBACK)",
}
_LATER = ("Run the statements one at a time, with values in place of variables. A batch with variables and "
          "control flow runs in a Fabric Warehouse, not on the local engine yet.")


def split_batches(sql: str) -> list[str]:
    """The script's batches: the text between ``GO`` lines."""
    return [part for part in _GO.split(sql) if part.strip()]


def translate(sql: str, columns: dict[str, dict[str, dict[str, str]]] | None = None) -> tuple[list[str], list[str]]:
    """Each statement of the T-SQL script ``sql`` as DuckDB SQL, and notes on what may differ.

    ``columns`` is {schema: {table: {column: DuckDB type}}} for the tables the script may read; it
    lets divisions and ``+`` follow T-SQL's types. Raises ValueError for what isn't carried."""
    texts: list[str] = []
    notes: list[str] = []
    schema = _schema(columns or {})
    for batch in split_batches(sql):
        for tree in _parse(batch):
            if _harmless_setting(tree):
                continue
            _refuse(tree)
            _temp_tables(tree)
            _converts(tree)
            notes += _types(tree, schema)
            notes += _likes(tree)
            _patindex(tree)
            _functions(tree)
            _gaps(tree)
            _string_edges(tree)
            try:
                text = tree.sql(dialect=KfDuckDB, unsupported_level=ErrorLevel.RAISE)
            except SqlglotError as exc:
                raise ValueError(f"This T-SQL can't be run on the local engine: {_first_line(exc)}") from None
            if text.strip():
                texts.append(text)
    if not texts:
        raise ValueError("The query is empty.")
    return texts, list(dict.fromkeys(notes))


class KfTSQL(TSQL):
    """T-SQL as SQL Server reads it: ``FLOAT`` is an 8-byte float, ``FLOAT(1)`` to ``FLOAT(24)`` and
    ``REAL`` 4 bytes. sqlglot reads ``FLOAT`` as ``REAL``, which DuckDB runs as 4 bytes, so
    ``CAST(0.1 AS FLOAT)`` came back as 0.10000000149."""

    class Tokenizer(TSQL.Tokenizer):  # type: ignore[misc,valid-type]  # sqlglot sets Tokenizer per dialect at run time
        KEYWORDS = {**TSQL.Tokenizer.KEYWORDS, "FLOAT": TokenType.DOUBLE}

    # In T-SQL, W is weekday (as DW), not week: DATEPART(w, d) is 1 to 7.
    DATE_PART_MAPPING = {**TSQL.DATE_PART_MAPPING, "W": "DAYOFWEEK"}


def _parse(batch: str) -> list[exp.Expression]:
    unsupported: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            unsupported.append(record.getMessage())

    handler = Catch(logging.WARNING)
    log = logging.getLogger("sqlglot")
    log.addHandler(handler)
    try:
        trees = [t for t in sqlglot.parse(batch, read=KfTSQL) if t is not None]
    except SqlglotError as exc:
        raise ValueError(f"Could not read the query as T-SQL: {_first_line(exc)}") from None
    finally:
        log.removeHandler(handler)
    if unsupported:
        m = re.match(r"'(\S+)", unsupported[0])
        word = (m[1] if m else batch.strip().split(None, 1)[0]).upper()
        raise ValueError(f"The local engine doesn't run T-SQL {word} yet. {_LATER}")
    return trees


def _harmless_setting(tree: exp.Expression) -> bool:
    """``SET NOCOUNT ON`` and other session options that change nothing in a result."""
    if not isinstance(tree, exp.Set):
        return False
    items = tree.expressions
    names = [i.this.this.name.upper() if isinstance(i.this, exp.EQ) and isinstance(i.this.this, exp.Column) else ""
             for i in items]
    if names == ["DATEFIRST"]:
        value = items[0].this.expression
        if isinstance(value, exp.Literal) and value.name == "7":
            return True  # Fabric's default, which the translation already follows
        raise ValueError("The local engine counts weeks from Sunday (SET DATEFIRST 7, Fabric's default), and doesn't "
                         "run other DATEFIRST settings yet. Remove the SET DATEFIRST.")
    return bool(items) and all(n in _DROPPED_SETTINGS for n in names)


def _refuse(tree: exp.Expression) -> None:
    for kind, what in _REFUSED.items():
        if isinstance(tree, kind):
            raise ValueError(f"The local engine doesn't run T-SQL {what} yet. {_LATER}")
    if isinstance(tree, exp.Command):
        word = (tree.name or "").upper()
        raise ValueError(f"The local engine doesn't run T-SQL {word or 'this statement'} yet. {_LATER}")
    if isinstance(tree, exp.Set):
        raise ValueError(f"The local engine doesn't run T-SQL SET yet. {_LATER}")
    if isinstance(tree, exp.Create) and (tree.args.get("kind") or "").upper() in ("PROCEDURE", "PROC", "FUNCTION",
                                                                                   "TRIGGER"):
        raise ValueError(f"T-SQL CREATE {tree.args['kind'].upper()} doesn't run on the local engine. "
                         "Procedures and functions run in a Fabric Warehouse.")
    for p in tree.find_all(exp.Parameter, exp.SessionParameter):
        name = p.sql(dialect="tsql")
        raise ValueError(f"The local engine doesn't run T-SQL variables ({name}) yet. {_LATER}")


def _temp_tables(tree: exp.Expression) -> None:
    """``#t`` reads and makes the temporary table ``"#t"``, not an ordinary table ``t``."""
    for ident in tree.find_all(exp.Identifier):
        if ident.args.get("temporary"):
            ident.set("this", "#" + ident.this)
            ident.set("quoted", True)
            ident.set("temporary", None)
    if isinstance(tree, exp.Create):
        name = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
        if isinstance(name, exp.Table) and name.name.startswith("#"):
            props = tree.args.get("properties") or exp.Properties(expressions=[])
            if not any(isinstance(p, exp.TemporaryProperty) for p in props.expressions):
                props.append("expressions", exp.TemporaryProperty())
                tree.set("properties", props)


def _schema(columns: dict[str, dict[str, dict[str, str]]]) -> Any:
    from sqlglot.schema import MappingSchema
    clean: dict[str, dict[str, dict[str, str]]] = {}
    for schema, tables in columns.items():
        for table, cols in tables.items():
            kept = {}
            for col, kind in cols.items():
                try:
                    exp.DataType.build(kind, dialect="duckdb")
                except SqlglotError:
                    continue  # a nested type sqlglot can't read; that column stays untyped
                kept[col] = kind
            clean.setdefault(schema, {})[table] = kept
    return MappingSchema(clean, dialect="duckdb", normalize=True)


def _types(tree: exp.Expression, schema: Any) -> list[str]:
    """Make integer division and averages, ``+`` on text, and casts to integers behave as in T-SQL. The types come from
    a qualified copy of the tree (qualifying renames output columns, so the copy isn't run)."""
    from sqlglot.optimizer.annotate_types import annotate_types
    from sqlglot.optimizer.qualify import qualify

    kinds = (exp.Div, exp.Avg, exp.Add, exp.Cast, exp.TryCast, exp.DateAdd, exp.TimestampTrunc, exp.Pow)
    here = _in_order(tree, kinds)
    if not here:
        return []
    sized = [n for n in here if isinstance(n, (exp.Cast, exp.TryCast))]
    typed = tree.copy()
    try:
        typed = qualify(typed, schema=schema, dialect="duckdb", db="dbo", expand_stars=False,
                        validate_qualify_columns=False, quote_identifiers=False, identify=False)
    except SqlglotError:
        pass  # unknown tables: literals still get their types
    try:
        typed = annotate_types(typed, schema=schema, dialect="duckdb")
    except SqlglotError:
        for node in sized:
            _sized_text(node, None)
        return ["Couldn't tell the column types, so divisions, averages and casts to integers are done as DuckDB "
                "does them (T-SQL divides and averages integers as integers, and cuts a decimal cast to an integer "
                "toward zero)."]
    there = _in_order(typed, kinds)
    if len(there) != len(here):
        for node in sized:
            _sized_text(node, None)
        return ["Couldn't match the query's columns to their types, so divisions, averages and casts to "
                "integers are done as DuckDB does them (T-SQL divides and averages integers as integers, and cuts a "
                "decimal cast to an integer toward zero)."]
    notes: list[str] = []
    for node, info in zip(here, there):
        if isinstance(node, exp.Div):
            a, b = info.this.type, info.expression.type
            if _is(a, _INTEGERS) and _is(b, _INTEGERS):
                node.replace(exp.IntDiv(this=node.this, expression=node.expression))
            elif _unknown(a) or _unknown(b):
                notes.append(f"Couldn't tell the types in {node.sql(dialect='tsql')}: it divides as DuckDB does, "
                             "and T-SQL divides two integers as integers.")
        elif isinstance(node, exp.Avg):
            if _is(info.this.type, _INTEGERS):
                # AVG(x) OVER (...) is a Window around the AVG: the whole window is what gets cut.
                whole = node.parent if isinstance(node.parent, exp.Window) and node.parent.this is node else node
                whole.replace(exp.Cast(this=exp.Trunc(this=whole.copy()), to=exp.DataType.build("BIGINT")))
        elif isinstance(node, exp.Pow):
            notes += _power(node, info.this.type)
        elif isinstance(node, (exp.DateAdd, exp.TimestampTrunc)):
            if _is(info.this.type, {exp.DataType.Type.DATE}):
                _stays_a_date(node)
        elif isinstance(node, exp.Add):
            a, b = info.this.type, info.expression.type
            if _is(a, _TEXT) and _is(b, _TEXT):
                node.replace(exp.DPipe(this=node.this, expression=node.expression, safe=False))
            elif _is(a, _TEXT) and _is(b, _NUMBERS):
                # A number outranks text in T-SQL: '1' + 1 is 2 (and 'x' + 1 is an error).
                node.set("this", exp.Cast(this=node.this, to=b.copy()))
            elif _is(b, _TEXT) and _is(a, _NUMBERS):
                node.set("expression", exp.Cast(this=node.expression, to=a.copy()))
            elif (_is(a, _TEXT) or _is(b, _TEXT)) and (_unknown(a) or _unknown(b)):
                notes.append(f"Couldn't tell the types in {node.sql(dialect='tsql')}: T-SQL adds a number to text "
                             "as numbers and joins two strings, and here it is done as DuckDB does it.")
        else:
            to = node.args.get("to")
            if isinstance(to, exp.DataType) and to.this in _INTEGERS - {exp.DataType.Type.BIT} \
                    and _unknown(info.this.type):
                notes.append(f"Couldn't tell the type in {node.sql(dialect='tsql')}: it converts as DuckDB does, "
                             "and T-SQL cuts a decimal toward zero and refuses text that isn't a whole number.")
            _truncated(node, info.this.type)
            _sized_text(node, info.this.type)  # a no-op for the integer casts _truncated may replace
    return notes


def _power(node: exp.Pow, base: exp.DataType | None) -> list[str]:
    """``POWER(x, y)`` has x's type in T-SQL: an integer base gives an integer, cut toward zero
    (``POWER(2, -1)`` is 0), and a result too big for that type is an error. A decimal base keeps the
    decimal's scale, which isn't carried here, so it is refused rather than answered differently."""
    first = node.this.this if isinstance(node.this, exp.Neg) else node.this
    written_decimal = isinstance(first, exp.Literal) and not first.is_string and "." in first.name \
        and "e" not in first.name.lower()  # 2.0 is a decimal in T-SQL, 2e0 a float
    if _is(base, _INTEGERS - {exp.DataType.Type.BIT}) and not written_decimal:
        assert base is not None
        big = base.this == exp.DataType.Type.BIGINT  # tinyint and smallint give an int, as T-SQL's POWER does
        node.replace(exp.Cast(this=exp.Trunc(this=node.copy()), to=exp.DataType.build("BIGINT" if big else "INT")))
    elif written_decimal or _is(base, {exp.DataType.Type.DECIMAL, exp.DataType.Type.MONEY,
                                        exp.DataType.Type.SMALLMONEY}):
        raise ValueError(_NOT_YET + "POWER of a decimal keeps the decimal's scale in T-SQL, which isn't mapped "
                         "yet. Cast the first argument to FLOAT, or to the integer type you mean.")
    elif _unknown(base):
        return [f"Couldn't tell the type in {node.sql(dialect='tsql')}: it raises as DuckDB does, in floating "
                "point, and T-SQL gives the first argument's type (an integer base gives an integer)."]
    return []


def _truncated(node: exp.Expression, source: exp.DataType | None) -> None:
    """``CAST(3.7 AS INT)`` is 3 in T-SQL, which cuts a decimal or float toward zero on the way to an
    integer type; DuckDB rounds it to 4. TRUNC first, so -3.7 is -3 as well."""
    to = node.args.get("to")
    if not (isinstance(to, exp.DataType) and to.this in _INTEGERS - {exp.DataType.Type.BIT}):
        return
    if _is(source, _NUMBERS - _INTEGERS):
        node.set("this", exp.Trunc(this=node.this))
    elif _is(source, _TEXT):
        _whole_text(node, to)


def _whole_text(node: exp.Expression, to: exp.DataType) -> None:
    """Text to an integer type: T-SQL takes only a whole number, with spaces around it, and '' as 0;
    ``CAST('3.7' AS INT)`` fails (TRY_CAST gives NULL), where DuckDB rounds it to 4. The text is
    bound once, as LIST_TRANSFORM([text], v -> ...)[1], so a source that changes each time it's read
    is read once."""
    v = exp.to_identifier("kf_text")
    whole = exp.func("REGEXP_FULL_MATCH", v.copy(), exp.Literal.string(" *[+-]?[0-9]+ *"))
    blank = exp.EQ(this=exp.func("TRIM", v.copy()), expression=exp.Literal.string(""))
    message = exp.Literal.string(f"Conversion failed when converting the text to data type {to.sql(dialect='tsql')}.")
    other = exp.Null() if isinstance(node, exp.TryCast) else exp.func("ERROR", message)
    cast = node.copy()
    cast.set("this", v.copy())
    case = exp.Case(ifs=[exp.If(this=whole, true=cast),
                         exp.If(this=blank, true=exp.Cast(this=exp.Literal.number(0), to=to.copy())),
                         exp.If(this=exp.Is(this=v.copy(), expression=exp.Null()),
                                true=exp.Cast(this=exp.Null(), to=to.copy()))],
                    default=other)
    bound = exp.func("LIST_TRANSFORM", exp.Array(expressions=[node.this]), exp.Lambda(this=case, expressions=[v]))
    node.replace(exp.Bracket(this=bound, expressions=[exp.Literal.number(1)], offset=1))  # DuckDB lists count from 1


_SIZED = {exp.DataType.Type.VARCHAR, exp.DataType.Type.NVARCHAR, exp.DataType.Type.CHAR, exp.DataType.Type.NCHAR}
_PADDED = {exp.DataType.Type.CHAR, exp.DataType.Type.NCHAR}


def _sized_text(node: exp.Expression, source: exp.DataType | None) -> None:
    """``CAST(x AS VARCHAR(3))`` cuts text to 3 characters in T-SQL (and ``CHAR(3)`` pads it); DuckDB
    ignores the length. With no length a CAST or CONVERT to text is 30 characters long. A number too
    long for the length is an error, not cut."""
    to = node.args.get("to")
    if not isinstance(to, exp.DataType) or to.this not in _SIZED:
        return
    size = to.expressions[0].this if to.expressions else exp.Literal.number(30)
    if not (isinstance(size, exp.Literal) and not size.is_string):
        to.set("expressions", [])  # VARCHAR(MAX): DuckDB's VARCHAR has no limit and takes no MAX
        return
    n = int(size.name)
    text = exp.Cast(this=node.this, to=exp.DataType.build("VARCHAR"), safe=isinstance(node, exp.TryCast))
    if isinstance(node, exp.TryCast):
        text = exp.TryCast(this=node.this, to=exp.DataType.build("VARCHAR"))
    if _is(source, _NUMBERS):
        too_long = exp.GT(this=exp.func("LENGTH", text.copy()), expression=exp.Literal.number(n))
        message = exp.Literal.string(f"The number doesn't fit in {to.sql(dialect='tsql')}.")
        cut: exp.Expression = exp.Case(ifs=[exp.If(this=too_long, true=exp.func("ERROR", message))], default=text)
    else:
        cut = exp.func("LEFT", text, exp.Literal.number(n))
    if to.this in _PADDED:
        cut = exp.func("RPAD", cut, exp.Literal.number(n), exp.Literal.string(" "))
    node.replace(cut)


def _converts(tree: exp.Expression) -> None:
    """``CONVERT`` fails on a value it can't convert, as ``CAST`` does; sqlglot writes it as TRY_CAST,
    which gives NULL instead. Write it as sqlglot does, then make the outer conversion strict."""
    for node in list(tree.find_all(exp.Convert)):
        try:
            new = sqlglot.parse_one(node.sql(dialect=KfDuckDB, unsupported_level=ErrorLevel.RAISE), read="duckdb")
        except SqlglotError as exc:
            raise ValueError(f"This T-SQL can't be run on the local engine: {_first_line(exc)}") from None
        if isinstance(new, (exp.Cast, exp.TryCast)):
            # Keep CONVERT's own type, length included (the DuckDB text drops it), and TRY_CONVERT's NULL.
            kind = exp.TryCast if node.args.get("safe") else exp.Cast
            new = kind(this=new.this, to=node.this.copy())
        node.replace(new)


_LIKE_NOTE = ("A LIKE pattern that isn't written in the query (such as LIKE other_column) matches as DuckDB's LIKE "
              "does, which takes no [a-c] character ranges.")


def _likes(tree: exp.Expression) -> list[str]:
    """T-SQL's ``LIKE`` takes ``[a-c]`` and ``[^a-c]`` character ranges, which DuckDB's doesn't: there
    ``'b1' LIKE '[a-c]1'`` is false. A pattern with a range is matched as a regular expression instead."""
    notes: list[str] = []
    for like in list(tree.find_all(exp.Like)):
        escape = like.parent if isinstance(like.parent, exp.Escape) else None
        pattern = like.expression
        if not (isinstance(pattern, exp.Literal) and pattern.is_string):
            notes.append(_LIKE_NOTE)
            continue
        char = escape.expression.name if escape is not None and isinstance(escape.expression, exp.Literal) else ""
        if "[" not in pattern.name or (escape is not None and len(char) != 1):
            continue
        match: exp.Expression = exp.RegexpFullMatch(this=like.this, expression=exp.Literal.string(_like_regex(pattern.name, char)))
        if like.args.get("negate"):
            match = exp.Not(this=match)
        (escape or like).replace(match)
    return notes


def _patindex(tree: exp.Expression) -> None:
    """``PATINDEX(pattern, s)``: where in ``s`` the LIKE pattern first matches, from 1, or 0; DuckDB has
    no PATINDEX. A pattern that starts with ``%`` is looked for anywhere, the rest of it matched from
    there to the end (``PATINDEX('%b%', 'abc')`` is 2); without one it must match from the start, so the
    answer is 1 or 0. NULL in, NULL out. The pattern has to be a literal."""
    for node in list(tree.find_all(exp.Anonymous)):
        if node.name.upper() != "PATINDEX":
            continue
        if len(node.expressions) != 2:
            raise ValueError("This T-SQL can't be run on the local engine: PATINDEX takes a pattern and a text.")
        pattern, text = node.expressions
        if not (isinstance(pattern, exp.Literal) and pattern.is_string):
            raise ValueError("This T-SQL can't be run on the local engine: PATINDEX needs its pattern written out "
                             "as text ('%b%'), not taken from a column or an expression.")
        core = pattern.name.lstrip("%")
        body = _like_regex(core)[len("(?s)"):]
        if not core:  # '%' alone matches from the first character (UNVERIFIED for an empty text)
            found: exp.Expression = exp.Literal.number(1)
        elif core == pattern.name:  # anchored at the start: the whole text has to match
            found = exp.Case(
                ifs=[exp.If(this=exp.RegexpFullMatch(this=text.copy(), expression=exp.Literal.string("(?s)" + body)),
                            true=exp.Literal.number(1))], default=exp.Literal.number(0))
        else:  # the shortest lead-in after which the rest matches to the end
            before = exp.func("REGEXP_REPLACE", text.copy(), exp.Literal.string(f"(?s)^(.*?){body}$"),
                              exp.Literal.string("\\1"))
            # Anonymous: LENGTH here counts trailing spaces (LEN, which _functions rewrites, doesn't).
            length = exp.Anonymous(this="LENGTH", expressions=[before])
            matches = exp.RegexpFullMatch(this=text.copy(), expression=exp.Literal.string("(?s).*" + body))
            found = exp.Case(ifs=[exp.If(this=matches, true=length + exp.Literal.number(1))],
                             default=exp.Literal.number(0))
        node.replace(exp.Case(ifs=[exp.If(this=exp.Is(this=text.copy(), expression=exp.null()), true=exp.null())],
                              default=found))


def _like_regex(pattern: str, escape: str = "") -> str:
    """A T-SQL LIKE pattern as an RE2 expression for a whole-string match."""
    out, i = ["(?s)"], 0  # % and _ match a line break too
    while i < len(pattern):
        c = pattern[i]
        if escape and c == escape and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if c == "[" and (found := _like_range(pattern, i + 1, escape)) is not None:
            text, i = found
            out.append(text)
            continue
        out.append(".*" if c == "%" else "." if c == "_" else re.escape(c))
        i += 1
    return "".join(out)


_NOTHING, _ANY = "[^\\x{0}-\\x{10FFFF}]", "[\\x{0}-\\x{10FFFF}]"


def _like_range(pattern: str, start: int, escape: str) -> tuple[str, int] | None:
    """The ``[...]`` that opens before ``start`` as an RE2 class, and where the pattern goes on; None
    when it isn't closed (the ``[`` is then an ordinary character). The escape character counts inside
    too, and a range written backwards (``[z-a]``) matches nothing, as in SQL Server."""
    chars: list[tuple[str, bool]] = []  # each character, and whether it was escaped
    i = start
    while i < len(pattern):
        c = pattern[i]
        if escape and c == escape and i + 1 < len(pattern):
            chars.append((pattern[i + 1], True))
            i += 2
            continue
        if c == "]" and chars:
            break
        chars.append((c, False))
        i += 1
    else:
        return None
    negate = len(chars) > 1 and chars[0] == ("^", False)
    if negate:
        chars = chars[1:]
    parts: list[str] = []
    k = 0
    while k < len(chars):
        if k + 2 < len(chars) and chars[k + 1] == ("-", False):
            low, high = chars[k][0], chars[k + 2][0]
            if low <= high:
                parts.append(_in_class(low) + "-" + _in_class(high))
            k += 3
        else:
            parts.append(_in_class(chars[k][0]))
            k += 1
    if not parts:  # only backwards ranges
        return (_ANY if negate else _NOTHING), i + 1
    return "[" + ("^" if negate else "") + "".join(parts) + "]", i + 1


def _in_class(ch: str) -> str:
    return "\\" + ch if ch in "\\[]^-" else ch


def _in_order(tree: exp.Expression, kinds: tuple) -> list[exp.Expression]:
    # Depth first: qualifying wraps columns in aliases, which moves nodes between levels but not in this order.
    return [n for n in tree.walk(bfs=False) if isinstance(n, kinds)]


def _is(kind: exp.DataType | None, group: set) -> bool:
    return kind is not None and kind.this in group


def _unknown(kind: exp.DataType | None) -> bool:
    return kind is None or kind.this in (exp.DataType.Type.UNKNOWN, exp.DataType.Type.NULL)


_SPREADS = {"VAR": exp.Variance, "VARP": exp.VariancePop, "STDEVP": exp.StddevPop}


def _functions(tree: exp.Expression) -> None:
    for kind in tree.find_all(exp.DataType):
        if kind.this == exp.DataType.Type.DOUBLE and kind.expressions:
            # FLOAT(n): 4 bytes up to 24 bits of mantissa, 8 from 25 to 53, as SQL Server stores it.
            bits = kind.expressions[0].this
            if not (isinstance(bits, exp.Literal) and bits.is_int and 1 <= int(bits.name) <= 53):
                raise ValueError("FLOAT(n) takes a whole number from 1 to 53.")
            small = int(bits.name) <= 24
            kind.set("this", exp.DataType.Type.FLOAT if small else exp.DataType.Type.DOUBLE)
            kind.set("expressions", [])
        # NVARCHAR(MAX) and VARBINARY(MAX): DuckDB's types have no limit and take no MAX.
        if any(isinstance(p.this, exp.Var) and p.this.name.upper() == "MAX" for p in kind.expressions
               if isinstance(p, exp.DataTypeParam)):
            kind.set("expressions", [])
    for join in tree.find_all(exp.Join):
        # CROSS / OUTER APPLY come out as a LATERAL join, which DuckDB wants an ON for.
        apply = join.this.args.get("cross_apply") if isinstance(join.this, exp.Lateral) else None
        if apply is not None:
            join.this.set("cross_apply", None)
            join.set("side", None if apply else "LEFT")
            join.set("on", exp.true())
    for node in list(tree.find_all(exp.Length, exp.Extract, exp.CurrentTimestamp, exp.Anonymous, exp.DateDiff,
                                   exp.DateAdd, exp.TimestampTrunc)):
        if isinstance(node, exp.DateDiff):
            part = _datepart(node, "DATEDIFF")
            if part == "WEEK":
                _sunday_weeks(node)
            elif part == "ISO_WEEK":
                _sunday_weeks(node, shift=0)
        elif isinstance(node, exp.DateAdd):
            _datepart(node, "DATEADD")
        elif isinstance(node, exp.TimestampTrunc):
            if _datepart(node, "DATETRUNC") == "WEEK":
                _sunday_week_start(node)
        elif isinstance(node, exp.Length):
            # LEN doesn't count trailing spaces.
            node.set("this", exp.func("RTRIM", node.this))
        elif isinstance(node, exp.Extract):
            unit = node.this.name.upper()
            if isinstance(node.expression, exp.Literal) and node.expression.is_string:
                node.set("expression", exp.cast(node.expression, "TIMESTAMP"))  # T-SQL reads the text as a datetime
            day = node.expression
            if unit in ("MILLISECOND", "MICROSECOND"):  # within the second, as T-SQL counts them (DuckDB adds the seconds)
                node.replace(exp.paren(exp.Mod(this=node.copy(), expression=exp.Literal.number(
                    1000 if unit == "MILLISECOND" else 1_000_000)), copy=False))
            elif unit in ("DAYOFWEEK", "DW", "WEEKDAY"):  # Sunday = 1 (DATEFIRST 7)
                node.replace(exp.paren(exp.func("DAYOFWEEK", day) + 1, copy=False))
            elif unit in ("WEEK", "WK", "WW"):  # 1 January is in week 1; weeks start on Sunday
                sunday_first = exp.EQ(this=exp.func("DAYOFWEEK", exp.func("DATE_TRUNC", exp.Literal.string("year"),
                                                                          day.copy())),
                                      expression=exp.Literal.number(0))
                node.replace(exp.paren(
                    exp.cast(exp.func("STRFTIME", day, exp.Literal.string("%U")), "INT")
                    + exp.Case(ifs=[exp.If(this=sunday_first, true=exp.Literal.number(0))],
                               default=exp.Literal.number(1)), copy=False))
        elif isinstance(node, exp.Anonymous) and node.name.upper() in _SPREADS and len(node.expressions) == 1:
            # VAR is the sample variance, VARP the population's, STDEVP the population's standard deviation
            # (STDEV is read as the sample's already). DuckDB has none of these names.
            node.replace(_SPREADS[node.name.upper()](this=node.expressions[0]))
        elif isinstance(node, exp.CurrentTimestamp) or (
                isinstance(node, exp.Anonymous) and node.name.upper() in ("GETUTCDATE", "SYSUTCDATETIME")):
            # A Fabric Warehouse's clock is UTC, and GETDATE() has no time zone. The project runs in UTC.
            node.replace(exp.cast(exp.CurrentTimestamp(), "TIMESTAMP"))


_NOT_YET = "This T-SQL can't be run on the local engine: "


def _gaps(tree: exp.Expression) -> None:
    """T-SQL functions DuckDB doesn't have, written as DuckDB SQL that gives T-SQL's answer; or, where
    that can't be done faithfully yet, refused with a message that says so (not DuckDB's own error)."""
    for kind in list(tree.find_all(exp.DataType)):
        if kind.this == exp.DataType.Type.BIT:  # DuckDB's BIT is a bit string: CAST(1 AS BIT) was 32 characters
            kind.replace(exp.DataType.build("BOOLEAN"))
    for node in list(tree.find_all(exp.Cast, exp.TryCast)):
        to, value = node.to, node.this
        if to.this == exp.DataType.Type.BOOLEAN and isinstance(value, exp.Literal) and value.is_string:
            text = value.name.strip().upper()
            if text in ("TRUE", "FALSE"):  # T-SQL converts 'true' and 'false', and numbers written as text
                node.replace(exp.Boolean(this=text == "TRUE"))
            else:
                inner = exp.TryCast if isinstance(node, exp.TryCast) else exp.Cast
                node.set("this", inner(this=value, to=exp.DataType.build("DECIMAL(38, 10)")))
    for rounded in list(tree.find_all(exp.Round)):
        mode = rounded.args.get("truncate")
        if mode is None:
            continue
        if not (isinstance(mode, exp.Literal) and not mode.is_string):
            raise ValueError(_NOT_YET + "ROUND's third argument has to be a number written in the query.")
        rounded.set("truncate", None)
        if mode.name not in ("0", "0.0"):  # ROUND(x, n, 1): cut, not round
            rounded.replace(exp.Anonymous(this="TRUNC", expressions=[rounded.this, rounded.args.get("decimals")
                                                                  or exp.Literal.number(0)]))
    for limit in list(tree.find_all(exp.Limit)):
        options = limit.args.get("limit_options")
        if options is not None and options.args.get("with_ties"):
            _with_ties(limit)
    for call in list(tree.find_all(exp.Anonymous)):
        name = call.name.upper()
        args = call.expressions
        if name == "CHOOSE" and len(args) >= 2:
            call.replace(exp.Case(this=args[0], ifs=[exp.If(this=exp.Literal.number(i), true=a)
                                                     for i, a in enumerate(args[1:], 1)]))
        elif name == "DATALENGTH" and len(args) == 1:
            call.replace(_datalength(args[0]))
        elif name == "STRING_SPLIT":
            _string_split(call)
        elif name == "ISNUMERIC":
            raise ValueError(_NOT_YET + "ISNUMERIC isn't mapped yet (its rules accept '$', ',' and '-' alone). "
                             "Use TRY_CAST(x AS FLOAT) IS NOT NULL, or TRY_CAST to the type you need.")


def _datalength(value: exp.Expression) -> exp.Expr:
    """``DATALENGTH``: the bytes of the text as UTF-8, as a warehouse's varchar holds it; an N'...'
    literal is nvarchar, two bytes a character (UTF-16)."""
    if isinstance(value, exp.National):
        return exp.Literal.number(len(value.name.encode("utf-16-le")))
    if isinstance(value, exp.Literal) and not value.is_string:
        raise ValueError(_NOT_YET + "DATALENGTH runs here only on text: a number's size depends on its T-SQL type.")
    return exp.func("OCTET_LENGTH", exp.Anonymous(this="ENCODE", expressions=[value]))


def _with_ties(limit: exp.Limit) -> None:
    """``TOP n WITH TIES``: the first n rows by the ORDER BY and every row tied with the last of them,
    as a QUALIFY on their rank (DuckDB has no WITH TIES)."""
    select = limit.parent
    order = select.args.get("order") if isinstance(select, exp.Select) else None
    options = limit.args["limit_options"]
    if not isinstance(select, exp.Select) or order is None or options.args.get("percent") or select.args.get("distinct") or select.args.get("qualify"):
        raise ValueError(_NOT_YET + "TOP ... WITH TIES runs here only with an ORDER BY and without PERCENT, "
                         "DISTINCT or QUALIFY.")
    rank = exp.Window(this=exp.Anonymous(this="RANK", expressions=[]), order=order.copy())
    select.set("limit", None)
    select.set("qualify", exp.Qualify(this=exp.LTE(this=rank, expression=limit.expression)))


def _string_split(node: exp.Anonymous) -> None:
    """``STRING_SPLIT(text, separator)`` as a table of one ``value`` column, one row per part."""
    if len(node.expressions) != 2:
        raise ValueError(_NOT_YET + "STRING_SPLIT with enable_ordinal isn't mapped yet.")
    separator = node.expressions[1]
    if isinstance(separator, (exp.Literal, exp.National)) and len(separator.name) != 1:
        raise ValueError("STRING_SPLIT takes a separator of one character, as in T-SQL.")
    parts = exp.Unnest(expressions=[exp.func("STRING_SPLIT", *node.expressions)])
    query = exp.select(exp.alias_(parts, "value")).subquery()
    holder = node.parent
    if isinstance(holder, exp.Table):  # FROM STRING_SPLIT(...) [AS s]
        alias = holder.args.get("alias")
        query.set("alias", alias.copy() if alias else exp.TableAlias(this=exp.to_identifier("string_split")))
        holder.replace(query)
    elif isinstance(holder, exp.Lateral):  # CROSS / OUTER APPLY STRING_SPLIT(...) AS s
        node.replace(query)
    else:
        raise ValueError(_NOT_YET + "STRING_SPLIT runs here only in FROM or CROSS APPLY.")


def _whole(node: exp.Expression | None, low: int) -> bool:
    """A whole-number literal of at least ``low``: then the edge rules can't apply."""
    return isinstance(node, exp.Literal) and not node.is_string and node.name.isdigit() and int(node.name) >= low


def _string_edges(tree: exp.Expression) -> None:
    """Where DuckDB's string functions answer differently from T-SQL's at the edges, as T-SQL does:

    - ``SUBSTRING(s, start, n)`` with a start below 1 counts the positions before 1 and returns only
      what falls from 1 on (``SUBSTRING('abc', 0, 2)`` is ``'a'``); DuckDB counts a negative start
      from the end. A negative length is an error.
    - ``STUFF(s, start, n, x)`` is NULL when the start is below 1 or past the end, or the length is
      negative; a NULL ``x`` deletes without inserting.
    - ``REPLICATE(s, n)`` is NULL when ``n`` is negative.

    Literal starts and lengths that can't hit an edge are left as they are."""
    for node in list(tree.find_all(exp.Substring, exp.Stuff, exp.Repeat)):
        if isinstance(node, exp.Substring):
            start, length = node.args.get("start"), node.args.get("length")
            if start is None or length is None or (_whole(start, 1) and _whole(length, 0)):
                continue
            cut: exp.Expression = node.copy()
            if not _whole(start, 1):
                first = exp.func("GREATEST", start.copy(), exp.Literal.number(1))
                count = exp.func("GREATEST", exp.paren(start.copy() + length.copy() - first.copy(), copy=False),
                                 exp.Literal.number(0))
                # GREATEST skips NULLs, so a NULL start or length is kept NULL first, as in T-SQL.
                unknown = exp.or_(exp.Is(this=start.copy(), expression=exp.null()),
                                  exp.Is(this=length.copy(), expression=exp.null()))
                cut = exp.Case(ifs=[exp.If(this=unknown, true=exp.null())],
                               default=exp.Substring(this=node.this, start=first, length=count))
            if not _whole(length, 0):
                negative = exp.LT(this=length.copy(), expression=exp.Literal.number(0))
                message = exp.Literal.string("Invalid length parameter passed to the SUBSTRING function.")
                cut = exp.Case(ifs=[exp.If(this=negative, true=exp.func("ERROR", message))], default=cut)
            node.replace(cut)
        elif isinstance(node, exp.Stuff):
            start, length = node.args["start"], node.args["length"]
            text = node.this
            past = exp.or_(exp.LT(this=start.copy(), expression=exp.Literal.number(1)),
                           exp.GT(this=start.copy(), expression=exp.func("LENGTH", text.copy())),
                           exp.LT(this=length.copy(), expression=exp.Literal.number(0)))
            node.set("expression", exp.func("COALESCE", node.expression, exp.Literal.string("")))
            node.replace(exp.Case(ifs=[exp.If(this=past, true=exp.null())], default=node.copy()))
        elif not _whole(node.args.get("times"), 0):
            times = node.args["times"]
            negative = exp.LT(this=times.copy(), expression=exp.Literal.number(0))
            node.replace(exp.Case(ifs=[exp.If(this=negative, true=exp.null())], default=node.copy()))


def _sunday_weeks(node: exp.DateDiff, shift: int = 1) -> None:
    """``DATEDIFF(week, a, b)`` counts the Sundays crossed from ``a`` to ``b``, whatever the time of day
    (DATEFIRST doesn't change it); DuckDB's counts whole weeks, so Saturday to Sunday was 0, not 1. A
    day later each Sunday is a Monday, which starts DuckDB's weeks: count the days between those.
    ``shift=0`` counts Mondays, for ``iso_week``."""
    def monday(day: exp.Expression) -> exp.Expression:
        shifted = exp.Add(this=exp.cast(day.copy(), "DATE"), expression=exp.Literal.number(shift))
        return exp.Anonymous(this="DATE_TRUNC", expressions=[exp.Literal.string("week"), shifted])

    days = exp.Anonymous(this="DATE_DIFF", expressions=[exp.Literal.string("day"), monday(node.expression),
                                                        monday(node.this)])
    node.replace(exp.paren(exp.IntDiv(this=days, expression=exp.Literal.number(7)), copy=False))


# T-SQL's date parts by every name it takes (sqlglot reads some as other dialects do: Y as year,
# W as week, M in DATETRUNC as minute), and the functions each may be used with.
_DATEPARTS = {name: part for part, names in {
    "YEAR": ("YEAR", "YY", "YYYY"), "QUARTER": ("QUARTER", "QQ", "Q"), "MONTH": ("MONTH", "MM", "M"),
    "DAYOFYEAR": ("DAYOFYEAR", "DY", "Y"), "DAY": ("DAY", "DD", "D"), "WEEK": ("WEEK", "WK", "WW"),
    "WEEKDAY": ("WEEKDAY", "DW", "W"), "HOUR": ("HOUR", "HH"), "MINUTE": ("MINUTE", "MI", "N"),
    "SECOND": ("SECOND", "SS", "S"), "MILLISECOND": ("MILLISECOND", "MS"), "MICROSECOND": ("MICROSECOND", "MCS"),
    "NANOSECOND": ("NANOSECOND", "NS"), "ISO_WEEK": ("ISO_WEEK", "ISOWK", "ISOWW"),
}.items() for name in names}
_TIME_PARTS = {"HOUR", "MINUTE", "SECOND", "MILLISECOND", "MICROSECOND", "NANOSECOND"}
_TAKES = {
    "DATEADD": set(_DATEPARTS.values()) - {"ISO_WEEK", "NANOSECOND"},
    "DATEDIFF": set(_DATEPARTS.values()) - {"NANOSECOND"},
    "DATETRUNC": set(_DATEPARTS.values()) - {"WEEKDAY", "NANOSECOND"},
}
# What each T-SQL part is in DuckDB: day of year and weekday count in days.
_DUCKDB_PART = {"DAYOFYEAR": "DAY", "WEEKDAY": "DAY", "ISO_WEEK": "WEEK"}


def _datepart(node: exp.Expression, function: str) -> str:
    """The T-SQL date part of ``node``, which is set to DuckDB's name for it. Raises ValueError for one T-SQL
    doesn't take there, and for nanoseconds, which the local engine doesn't keep."""
    unit = node.args.get("unit")
    name = unit.name.upper() if unit is not None else "DAY"
    part = _DATEPARTS.get(name)
    if part == "NANOSECOND":
        raise ValueError(f"{function} with nanosecond can't be run on the local engine: its times are kept to the "
                         "microsecond.")
    if part not in _TAKES[function]:
        raise ValueError(f"{function} doesn't take the date part {name.lower()}.")
    node.set("unit", exp.var(_DUCKDB_PART.get(part, part)))
    return part


def _stays_a_date(node: exp.Expression) -> None:
    """``DATEADD`` and ``DATETRUNC`` on a date give a date, as in T-SQL (DuckDB gives a timestamp). A time
    part on a date is an error there too."""
    unit = node.args.get("unit")
    name = unit.name.upper() if unit is not None else "DAY"
    if _DATEPARTS.get(name) in _TIME_PARTS:
        function = "DATEADD" if isinstance(node, exp.DateAdd) else "DATETRUNC"
        raise ValueError(f"{function} doesn't take the date part {name.lower()} for a date.")
    wrapper = exp.Cast(to=exp.DataType.build("DATE"))
    node.replace(wrapper)
    wrapper.set("this", node)  # the same node, so a later step still finds what's inside it


def _sunday_week_start(node: exp.TimestampTrunc) -> None:
    """``DATETRUNC(week, d)`` starts weeks on Sunday (DATEFIRST 7); DuckDB's start on Monday."""
    day = exp.Interval(this=exp.Literal.string("1"), unit=exp.var("DAY"))
    node.set("this", exp.Add(this=node.this, expression=day))
    wrapper = exp.Sub(expression=day.copy())
    node.replace(wrapper)
    wrapper.set("this", node)


def _first_line(exc: Any) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__

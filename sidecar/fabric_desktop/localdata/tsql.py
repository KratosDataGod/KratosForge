"""T-SQL on the local engine (FD-LDE-004): a T-SQL script read with sqlglot and run as DuckDB SQL,
with the differences that change answers translated, and anything that can't be carried refused.

sqlglot already turns most T-SQL into DuckDB SQL (TOP, brackets, ISNULL, IIF, DATEADD, CROSS
APPLY, SELECT INTO, OFFSET/FETCH). On top of that this module:

- divides integers as integers (``7 / 2`` is 3), and averages integers as integers, as T-SQL
  does, using the project's column types to tell. A division whose types it can't tell is left
  as DuckDB divides and reported in ``notes``;
- counts ``LEN`` without trailing spaces, numbers ``DATEPART(weekday)`` from Sunday = 1 and
  ``DATEPART(week)`` from the week of 1 January (Fabric's defaults: DATEFIRST 7, us_english);
- joins strings with ``+`` as T-SQL does when a side is known to be text;
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
            _functions(tree)
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

    kinds = (exp.Div, exp.Avg, exp.Add, exp.Cast, exp.TryCast)
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
        return ["Couldn't tell the column types, so divisions and averages are done as DuckDB does them "
                "(T-SQL divides and averages integers as integers)."]
    there = _in_order(typed, kinds)
    if len(there) != len(here):
        for node in sized:
            _sized_text(node, None)
        return ["Couldn't match the query's columns to their types, so divisions and averages are done as DuckDB "
                "does them (T-SQL divides and averages integers as integers)."]
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
            _truncated(node, info.this.type)
            _sized_text(node, info.this.type)  # a no-op for the integer casts _truncated may replace
    return notes


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
    ``CAST('3.7' AS INT)`` fails (TRY_CAST gives NULL), where DuckDB rounds it to 4."""
    text = node.this
    whole = exp.func("REGEXP_FULL_MATCH", text.copy(), exp.Literal.string(" *[+-]?[0-9]+ *"))
    blank = exp.EQ(this=exp.func("TRIM", text.copy()), expression=exp.Literal.string(""))
    message = exp.Literal.string(f"Conversion failed when converting the text to data type {to.sql(dialect='tsql')}.")
    other = exp.Null() if isinstance(node, exp.TryCast) else exp.func("ERROR", message)
    node.replace(exp.Case(ifs=[exp.If(this=whole, true=node.copy()),
                               exp.If(this=blank, true=exp.Cast(this=exp.Literal.number(0), to=to.copy())),
                               exp.If(this=exp.Is(this=text.copy(), expression=exp.Null()), true=exp.Cast(
                                   this=exp.Null(), to=to.copy()))],
                          default=other))


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
    for node in list(tree.find_all(exp.Length, exp.Extract, exp.CurrentTimestamp, exp.Anonymous)):
        if isinstance(node, exp.Length):
            # LEN doesn't count trailing spaces.
            node.set("this", exp.func("RTRIM", node.this))
        elif isinstance(node, exp.Extract):
            unit = node.this.name.upper()
            day = node.expression
            if unit in ("DAYOFWEEK", "DW", "WEEKDAY"):  # Sunday = 1 (DATEFIRST 7)
                node.replace(exp.paren(exp.func("DAYOFWEEK", day) + 1, copy=False))
            elif unit in ("WEEK", "WK", "WW"):  # 1 January is in week 1; weeks start on Sunday
                sunday_first = exp.EQ(this=exp.func("DAYOFWEEK", exp.func("DATE_TRUNC", exp.Literal.string("year"),
                                                                          day.copy())),
                                      expression=exp.Literal.number(0))
                node.replace(exp.paren(
                    exp.cast(exp.func("STRFTIME", day, exp.Literal.string("%U")), "INT")
                    + exp.Case(ifs=[exp.If(this=sunday_first, true=exp.Literal.number(0))],
                               default=exp.Literal.number(1)), copy=False))
        elif isinstance(node, exp.CurrentTimestamp) or (
                isinstance(node, exp.Anonymous) and node.name.upper() in ("GETUTCDATE", "SYSUTCDATETIME")):
            # A Fabric Warehouse's clock is UTC, and GETDATE() has no time zone. The project runs in UTC.
            node.replace(exp.cast(exp.CurrentTimestamp(), "TIMESTAMP"))


def _first_line(exc: Any) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__

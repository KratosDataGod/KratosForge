"""``spark.sql(sqlQuery, args=None, **kwargs)`` parameters, as PySpark 3.5 (Fabric runtime 1.3) and
Databricks take them.

``args`` binds parameter markers: a dict for named markers (``:name``), a list for positional ones
(``?``). Each value goes in as a typed literal, never as SQL text, so a value can't change the query.

``kwargs`` fill ``{name}`` fields as PySpark's SQLStringFormatter does: a DataFrame becomes a
temporary view's name, a plain column reference becomes the column's name, text becomes a quoted
string literal, and anything else is written as Python formats it.

Markers and fields are found with sqlglot's Spark tokenizer, so a ``:`` or ``?`` inside a string, a
quoted name or a comment is left alone. ``a:b`` with no space (Databricks' JSON path) isn't a
marker. Any error is a ValueError that names the marker or field.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import string
import uuid
from decimal import Decimal
from typing import Any, Callable

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A character right before ':' that makes it part of a path or cast (col:field, x::int), not a marker.
_PATH_BEFORE = re.compile(r"[A-Za-z0-9_`\])\"'.:]")
_COLUMN_PART = r'(?:[A-Za-z_]\w*|"(?:[^"]|"")+")'
_PLAIN_COLUMN = re.compile(rf'^(?:{_COLUMN_PART}\.)*({_COLUMN_PART})$')  # DuckDB may quote the relation: "unnamed_relation_1"."c"


def literal(value: Any, where: str) -> str:
    """``value`` as a Spark SQL literal of its own type, as PySpark's lit() gives it."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return f"{value}L" if not -2**31 <= value < 2**31 else str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return f"CAST('{'NaN' if math.isnan(value) else ('Infinity' if value > 0 else '-Infinity')}' AS DOUBLE)"
        return f"CAST({value!r} AS DOUBLE)"
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{where}: {value} isn't a value Spark's DECIMAL holds.")
        return f"{value}BD"
    if isinstance(value, dt.datetime):
        return f"TIMESTAMP '{value.isoformat(sep=' ')}'"
    if isinstance(value, dt.date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    raise ValueError(f"{where}: a {type(value).__name__} can't be a SQL parameter. Pass text, a number, a "
                     "boolean, None, a Decimal, a date or a datetime.")


def _markers(query: str) -> list[tuple[int, int, str]]:
    """(start, end, name) of each marker in ``query``: name '?' for a positional marker."""
    import sqlglot
    from sqlglot.tokens import TokenType
    try:
        tokens = sqlglot.tokenize(query, read="spark")
    except Exception as exc:  # noqa: BLE001  # silent-ok: an unreadable query is the caller's ValueError
        raise ValueError(f"spark.sql couldn't read the query to bind its parameters: {exc}") from None
    found = []
    for i, tok in enumerate(tokens):
        if tok.token_type == TokenType.PLACEHOLDER and tok.text == "?":
            found.append((tok.start, tok.end, "?"))
        elif tok.token_type == TokenType.COLON and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            before = query[tok.start - 1] if tok.start > 0 else " "
            if nxt.start == tok.end + 1 and _NAME.fullmatch(nxt.text) and not _PATH_BEFORE.match(before):
                found.append((tok.start, nxt.end, nxt.text))
    return found


def bind(query: str, args: Any) -> str:
    """``query`` with its markers replaced by ``args``' values (a dict or a list/tuple)."""
    markers = _markers(query)
    named = [m for m in markers if m[2] != "?"]
    positional = [m for m in markers if m[2] == "?"]
    if named and positional:
        raise ValueError("spark.sql: a query takes either named (:name) or positional (?) parameters, not both.")
    if isinstance(args, dict):
        if positional:
            raise ValueError("spark.sql: args is a dict, so the query's parameters are named (:name), not ?.")
        values = {}
        for _, _, name in named:
            if name not in args:
                raise ValueError(f"spark.sql: the query uses :{name}, and args has no value for it.")
            values[name] = literal(args[name], f"spark.sql args[{name!r}]")
        pieces = [(s, e, values[n]) for s, e, n in named]
    elif isinstance(args, (list, tuple)):
        if named:
            raise ValueError("spark.sql: args is a list, so the query's parameters are positional (?), not :name.")
        if len(args) != len(positional):
            raise ValueError(f"spark.sql: the query has {len(positional)} ? parameters and args has {len(args)} values.")
        pieces = [(s, e, literal(v, f"spark.sql args[{i}]")) for i, ((s, e, _), v) in enumerate(zip(positional, args, strict=True))]
    else:
        raise ValueError("spark.sql: args must be a dict (for :name parameters) or a list (for ? parameters).")
    for start, end, text in reversed(pieces):
        query = query[:start] + text + query[end + 1:]
    return query


def format_query(query: str, kwargs: dict[str, Any], register_view: Callable[[Any, str], None],
                 is_dataframe: Callable[[Any], bool], is_column: Callable[[Any], bool]) -> str:
    """``query``'s ``{name}`` fields filled from ``kwargs``, as PySpark's SQLStringFormatter fills them.
    A DataFrame is registered as a temporary view (``register_view(df, name)``) and its name written."""
    views: list[tuple[Any, str]] = []

    class _Formatter(string.Formatter):
        def get_field(self, field_name: str, args: Any, kw: Any) -> Any:
            obj, first = super().get_field(field_name, args, kw)
            return convert(obj, field_name), first

    def convert(value: Any, field: str) -> Any:
        if is_dataframe(value):
            for df, name in views:
                if df is value:
                    return name
            name = "_pyspark_" + uuid.uuid4().hex
            register_view(value, name)
            views.append((value, name))
            return name
        if is_column(value):
            text = str(getattr(value, "expr", value))
            match = _PLAIN_COLUMN.match(text)
            if not match:
                raise ValueError(f"{text} in {field} should be a plain column reference such as `df.col` or "
                                 "`col('column')`.")
            name = match.group(1)
            name = name[1:-1].replace('""', '"') if name.startswith('"') else name
            return "`" + name.replace("`", "``") + "`"
        if isinstance(value, str):
            return literal(value, field)
        return value

    return _Formatter().format(query, **kwargs)

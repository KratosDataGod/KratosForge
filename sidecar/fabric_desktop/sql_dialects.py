"""The SQL dialects the SQL query page knows, and which of them run on this PC.

Each Fabric engine speaks its own SQL. The page lets a person pick the one they are writing for, so
the editor, Format SQL and the compatibility check (sqlcompat) use that dialect's rules. Only Spark SQL
and DuckDB run here: Spark SQL is translated to DuckDB by sqlglot (sql.py), DuckDB SQL runs as is.
T-SQL for Fabric Warehouse, its SQL analytics endpoint and Fabric SQL database is edited and formatted here, never run, because
DuckDB is not the Warehouse engine and a local result would say nothing about Fabric's.
"""

from __future__ import annotations

import decimal
import os
import re
from typing import Any

DIALECTS: dict[str, dict[str, Any]] = {
    "spark": {"label": "Spark SQL (Lakehouse)", "sqlglot": "spark", "runs_here": True},
    "warehouse": {"label": "Fabric Warehouse (T-SQL)", "sqlglot": "tsql", "runs_here": False},
    "endpoint": {"label": "Fabric SQL analytics endpoint (T-SQL)", "sqlglot": "tsql", "runs_here": False},
    "sqldb": {"label": "Fabric SQL database (T-SQL)", "sqlglot": "tsql", "runs_here": False},
    "duckdb": {"label": "DuckDB (this PC)", "sqlglot": "duckdb", "runs_here": True},
}
DEFAULT = "spark"
# sqlglot underlines the bad token with terminal escape codes; the app shows the text, so they go.
_ESCAPES = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# sqlglot reads and writes a query by recursion, so brackets or operators nested too deeply (about 60 brackets)
# (SELECT ((((...1)))), or 1 + 1 + ... a thousand times) run out of Python's stack: a RecursionError,
# which no caller expected and so came out as a crash. It is this refusal instead.
TOO_DEEP = ("The query is nested too deeply to read on this PC: too many brackets, operators or calls "
            "inside one another. Split it into smaller parts, such as a WITH clause or a view.")


def parse_problem(exc: Exception) -> str:
    """A sqlglot parse error as plain text for a person: its message and the excerpt, without the
    terminal escape codes sqlglot puts around the bad token."""
    return _ESCAPES.sub("", str(exc))


# sqlglot reads a type's name followed by a bracket (string(x), int(x), struct(x), decimal(x)...) first as a
# type, then again as a call, and it does so at every level of nesting, so its time doubles with each level:
# twenty struct( or string( inside one another took more than a minute, and a few more would never end.
# A query nested deeper than this is refused (TOO_DEEP) before sqlglot reads it; at this depth it takes
# about a quarter of a second.
TYPE_CALL_DEPTH = 12


def _check_type_calls(tokens: list[Any], parser: Any) -> None:
    """A ParseError (``too_deep``) when calls named like a type sit more than TYPE_CALL_DEPTH deep."""
    from sqlglot.tokens import TokenType
    types = parser.TYPE_TOKENS
    opened: list[bool] = []  # per open bracket: whether a type's name is just before it
    depth = 0
    for i, token in enumerate(tokens):
        if token.token_type == TokenType.L_PAREN:
            typed = i > 0 and tokens[i - 1].token_type in types
            opened.append(typed)
            depth += typed
            if depth > TYPE_CALL_DEPTH:
                raise too_deep()
        elif token.token_type == TokenType.R_PAREN and opened:
            depth -= opened.pop()


def _check_tokens(sql: str, read: Any) -> None:
    """``sql``'s tokens checked for the nesting sqlglot takes too long to read (_check_type_calls). Text
    that can't be tokenized is left for sqlglot.parse to refuse."""
    if sql.count("(") <= TYPE_CALL_DEPTH:  # no deeper nesting of calls is possible: skip the second tokenize
        return
    from sqlglot.dialects.dialect import Dialect
    from sqlglot.errors import TokenError
    dialect = Dialect.get_or_raise(read)
    try:
        tokens = dialect.tokenize(sql)
    except TokenError:
        return
    _check_type_calls(tokens, dialect.parser_class)


def parse(sql: str, read: Any = None, **options: Any) -> list[Any]:
    """sqlglot.parse, with one of its crashes made a ParseError like any other unreadable SQL: a key and
    value function given an odd number of arguments (Snowflake's OBJECT_CONSTRUCT('a', 1, 'b') or VAR_MAP,
    Spark's map('a')) stops its build_var_map with an IndexError. Any other IndexError is raised as it is.
    A query nested deeper than sqlglot can read in good time, or at all, is a ParseError too (too_deep)."""
    import sqlglot
    try:
        _check_tokens(sql, read)
        statements = sqlglot.parse(sql, read=read, **options)
        _check_arguments(statements, read)
        _remember_spark_names(statements, sql, read)
    except IndexError as exc:
        raise _uneven_pairs(exc) from None
    except RecursionError:
        raise too_deep() from None
    except decimal.InvalidOperation:
        raise bad_number() from None
    except (AttributeError, TypeError, KeyError) as exc:
        raise _sqlglot_stopped(exc) from None
    return statements


def parse_one(sql: str, read: Any = None, **options: Any) -> Any:
    """sqlglot.parse_one, with the same ParseErrors as parse() for an odd number of key and value arguments
    and for a query nested too deeply."""
    import sqlglot
    try:
        _check_tokens(sql, read)
        statement = sqlglot.parse_one(sql, read=read, **options)
        _check_arguments([statement], read)
        _remember_spark_names([statement], sql, read)
    except IndexError as exc:
        raise _uneven_pairs(exc) from None
    except RecursionError:
        raise too_deep() from None
    except decimal.InvalidOperation:
        raise bad_number() from None
    except (AttributeError, TypeError, KeyError) as exc:
        raise _sqlglot_stopped(exc) from None
    return statement


def _sqlglot_stopped(exc: Exception) -> Exception:
    """A ParseError when ``exc`` was raised inside sqlglot itself, which stops this way on some malformed
    text (DuckDB's struct {:1} with no key name is an AttributeError in its _kv_to_prop_eq); ``exc`` as it is
    when it came from KratosForge's own code, which runs inside sqlglot.parse too. ``final``: the text
    was read the way it was meant to be and isn't read again as DuckDB SQL."""
    from sqlglot.errors import ParseError
    tb, last = exc.__traceback__, ""
    while tb is not None:
        last = tb.tb_frame.f_code.co_filename
        tb = tb.tb_next
    if f"{os.sep}sqlglot{os.sep}" not in last and "/sqlglot/" not in last:
        return exc
    error = ParseError("Part of the query couldn't be read. Check its brackets, quotes and commas.")
    error.final = True  # type: ignore[attr-defined]
    return error


def bad_number() -> Exception:
    """The ParseError for a number sqlglot can't read as a decimal (Spark's 1e10.5BD stopped its build_cast
    with decimal.InvalidOperation). ``final``: DuckDB SQL wouldn't read it either."""
    from sqlglot.errors import ParseError
    error = ParseError("A number in the query isn't written the way Spark SQL reads one (such as 1.5BD or 1e10).")
    error.final = True  # type: ignore[attr-defined]
    return error


def too_deep() -> Exception:
    """The ParseError for a query nested deeper than sqlglot can read (TOO_DEEP). Its ``too_deep`` is True,
    so sql.parse reports it instead of reading the query again as DuckDB SQL, which would run out of
    stack the same way."""
    from sqlglot.errors import ParseError
    error = ParseError(TOO_DEEP)
    error.too_deep = True  # type: ignore[attr-defined]
    return error


def _check_arguments(statements: list[Any], read: Any) -> None:
    """A ParseError naming the call when Spark SQL leaves out an argument it must have: sqlglot builds some
    calls without checking their arguments (datediff('2024-02-01') is a DateDiff with nothing to compare),
    and the rewrites after it then failed on the missing one with an AttributeError. Spark refuses such a
    call too (WRONG_NUM_ARGS). Its ``final`` is True, so sql.parse doesn't read it again as DuckDB SQL."""
    if read not in ("spark", "databricks"):
        return
    from sqlglot import exp
    from sqlglot.errors import ParseError
    for statement in statements:
        if not isinstance(statement, exp.Expression):
            continue
        for node in statement.walk():
            if isinstance(node, exp.Func) and node.error_messages():
                call = node
                while isinstance(call.parent, exp.Func) and call.meta.get("start") is None:
                    call = call.parent  # the call as written: sqlglot wraps arguments in its own conversions
                name = call.name if isinstance(call, exp.Anonymous) else call.sql_name()
                error = ParseError(f"{str(name).lower()}() is missing an argument it needs.")
                error.final = True  # type: ignore[attr-defined]
                raise error


def _remember_spark_names(statements: list[Any], sql: str, read: Any) -> None:
    """Spark SQL keeps the names its functions were called by and its constants as written, for the
    column names Spark gives (spark_sql_names)."""
    if read in ("spark", "databricks"):
        from . import spark_sql_names
        spark_sql_names.remember(statements, sql)


def _uneven_pairs(exc: IndexError) -> Exception:
    """The ParseError naming the function when ``exc`` came from sqlglot's build_var_map; else ``exc``.
    Its ``uneven_pairs`` is True, so sql.parse reports it instead of reading the query as DuckDB SQL."""
    from sqlglot.errors import ParseError
    name, last, tb = None, None, exc.__traceback__
    while tb is not None:
        last = tb.tb_frame.f_code.co_name
        if last == "_parse_function_call" and isinstance(tb.tb_frame.f_locals.get("upper"), str):
            name = tb.tb_frame.f_locals["upper"]  # the innermost function being read
        tb = tb.tb_next
    if last != "build_var_map":
        return exc
    error = ParseError(f"{name or 'A key and value function'} needs key and value pairs: an even number of "
                       "arguments, a key before each value.")
    error.uneven_pairs = True  # type: ignore[attr-defined]
    return error


def get(dialect: str | None) -> dict[str, Any]:
    """The dialect called ``dialect`` (the default when empty); a ValueError for an unknown one."""
    key = dialect or DEFAULT
    if key not in DIALECTS:
        raise ValueError(f"Unknown SQL dialect {key!r}. Dialects: {', '.join(DIALECTS)}.")
    return {"id": key, **DIALECTS[key]}


def sqlglot_name(dialect: str | None) -> str:
    return get(dialect)["sqlglot"]

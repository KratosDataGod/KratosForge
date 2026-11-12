"""The SQL dialects the SQL query page knows, and which of them run on this PC.

Each Fabric engine speaks its own SQL. The page lets a person pick the one they are writing for, so
the editor, Format SQL and the compatibility check (sqlcompat) use that dialect's rules. Only Spark SQL
and DuckDB run here: Spark SQL is translated to DuckDB by sqlglot (sql.py), DuckDB SQL runs as is.
T-SQL for Fabric Warehouse, its SQL analytics endpoint and Fabric SQL database is edited and formatted here, never run, because
DuckDB is not the Warehouse engine and a local result would say nothing about Fabric's.
"""

from __future__ import annotations

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


def parse_problem(exc: Exception) -> str:
    """A sqlglot parse error as plain text for a person: its message and the excerpt, without the
    terminal escape codes sqlglot puts around the bad token."""
    return _ESCAPES.sub("", str(exc))


def parse(sql: str, read: Any = None, **options: Any) -> list[Any]:
    """sqlglot.parse, with one of its crashes made a ParseError like any other unreadable SQL: a key and
    value function given an odd number of arguments (Snowflake's OBJECT_CONSTRUCT('a', 1, 'b') or VAR_MAP,
    Spark's map('a')) stops its build_var_map with an IndexError. Any other IndexError is raised as it is."""
    import sqlglot
    try:
        statements = sqlglot.parse(sql, read=read, **options)
    except IndexError as exc:
        raise _uneven_pairs(exc) from None
    _remember_spark_names(statements, sql, read)
    return statements


def parse_one(sql: str, read: Any = None, **options: Any) -> Any:
    """sqlglot.parse_one, with the same ParseError as parse() for an odd number of key and value arguments."""
    import sqlglot
    try:
        statement = sqlglot.parse_one(sql, read=read, **options)
    except IndexError as exc:
        raise _uneven_pairs(exc) from None
    _remember_spark_names([statement], sql, read)
    return statement


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

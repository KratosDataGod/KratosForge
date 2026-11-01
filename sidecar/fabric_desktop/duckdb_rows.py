"""Reading one row from DuckDB in a way that works on DuckDB 1.x and 2.0 (#1341).

DuckDB 2.0 keeps a connection busy while an executed result still has rows to give, so
``con.execute(...).fetchone()`` left the next query on that connection failing with "connection has
an unfinished result". Its relations no longer have ``fetchone()`` at all.
"""

from __future__ import annotations

from typing import Any

import duckdb


def duckdb_major() -> int:
    """DuckDB's major version: 1 or 2 while the engine runs on both (#1341)."""
    return int(duckdb.__version__.split(".")[0])


def keep_try_cast(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """``con`` with DuckDB 2.0's type_pushdown optimizer off. It pushes a TRY_CAST over a file's
    column into the scan as a strict type, so ``SELECT TRY_CAST(x AS DOUBLE) FROM read_csv(...)`` fails
    on a value that doesn't fit instead of giving NULL. DuckDB 1.x has no such optimizer (nor the name)."""
    if duckdb_major() >= 2:
        con.execute("SET disabled_optimizers = 'type_pushdown'")
    return con


def first_row(result: Any) -> tuple[Any, ...] | None:
    """The first row of an executed result or a relation, or None when there are none. The result is
    read to its end, which frees the connection on 2.0, so give it a query that returns few rows."""
    if isinstance(result, duckdb.DuckDBPyRelation):
        result = result.limit(1)
    rows = result.fetchall()
    return rows[0] if rows else None


def one_row(result: Any) -> tuple[Any, ...]:
    """The one row of a query that always returns one, such as an aggregate, read as first_row reads it."""
    row = first_row(result)
    if row is None:
        raise LookupError("The query returned no rows.")
    return row

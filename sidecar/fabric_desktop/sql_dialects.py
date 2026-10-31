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


def get(dialect: str | None) -> dict[str, Any]:
    """The dialect called ``dialect`` (the default when empty); a ValueError for an unknown one."""
    key = dialect or DEFAULT
    if key not in DIALECTS:
        raise ValueError(f"Unknown SQL dialect {key!r}. Dialects: {', '.join(DIALECTS)}.")
    return {"id": key, **DIALECTS[key]}


def sqlglot_name(dialect: str | None) -> str:
    return get(dialect)["sqlglot"]

"""What the SQL editor shows for a function: its forms (signatures) and what it does (editor parity S07, S11).

The hover card and the signature help read it, on this PC, with nothing run and nothing sent anywhere:

- Spark SQL: Spark 3.5's own usage line for each built-in function (spark_function_help.json, captured
  by tools/capture_spark_usage.py from Apache Spark, Apache License 2.0).
- T-SQL (Warehouse, SQL analytics endpoint, SQL database): tsql_function_help.json, the arguments and a
  one-line summary for each function the editor suggests, written from Microsoft's T-SQL reference.
- DuckDB: the engine's own catalog, duckdb_functions(), so it always matches the DuckDB that runs here.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
TSQL_DIALECTS = frozenset({"warehouse", "endpoint", "sqldb"})
MAX_FORMS = 6
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@functools.cache
def _table(file: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((HERE / file).read_text(encoding="utf-8"))
    data.pop("_source", None)
    return data


def _spark(name: str) -> list[dict[str, str]]:
    return [{"signature": sig, "summary": summary} for sig, summary in _table("spark_function_help.json").get(name, [])]


def _tsql(name: str) -> list[dict[str, str]]:
    entry = _table("tsql_function_help.json").get(name)
    if not entry:
        return []
    args, summary = entry
    return [{"signature": f"{name.upper()}({args})", "summary": summary}]


@functools.cache
def _duckdb(name: str) -> tuple[tuple[str, str], ...]:
    import duckdb  # the engine's DuckDB, so the forms match what runs here

    with duckdb.connect(":memory:") as con:
        rows = con.execute(
            "SELECT parameters, parameter_types, return_type, varargs, coalesce(description, '') "
            "FROM duckdb_functions() WHERE lower(function_name) = ? "
            "AND function_type IN ('scalar', 'aggregate', 'macro', 'table', 'table_macro') "
            "ORDER BY function_oid", [name]).fetchall()
    out: list[tuple[str, str]] = []
    for params, types, returns, varargs, description in rows:
        names = list(params or [])
        kinds = list(types or [])
        args = [f"{p} {t}" if t and t != "ANY" else p for p, t in zip(names, (kinds + [""] * len(names))[:len(names)], strict=True)]
        if varargs:
            args.append(f"{varargs}...")
        sig = f"{name}({', '.join(args)})" + (f" → {returns}" if returns else "")
        form = (sig, " ".join(str(description).split()))
        if form not in out:
            out.append(form)
    return tuple(out)


def function_help(dialect: str, name: str) -> dict[str, Any] | None:
    """{"name", "forms": [{"signature", "summary"}], "more": int} for a function in a dialect, or None
    when there's nothing to show. ``more`` counts the forms left out (DuckDB lists one per type)."""
    if not isinstance(name, str) or not NAME.match(name):
        return None
    key = name.lower()
    if dialect == "spark":
        forms = _spark(key)
    elif dialect in TSQL_DIALECTS:
        forms = _tsql(key)
    elif dialect == "duckdb":
        forms = [{"signature": s, "summary": d} for s, d in _duckdb(key)]
    else:
        return None
    if not forms:
        return None
    return {"name": key, "forms": forms[:MAX_FORMS], "more": max(0, len(forms) - MAX_FORMS)}

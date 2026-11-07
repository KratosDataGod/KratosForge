"""Which built-in functions answer in a Databricks SQL cell on this PC as they do in Databricks.

A Databricks SQL cell runs on the DuckDB engine: sqlglot reads it with its Databricks dialect, the
Spark fixes are made (engines.local_sql._databricks), and DuckDB runs it in UTC, the session zone
of a Databricks warehouse. ``coverage()`` sends every built-in of Spark 3.5 (``spark_builtins.json``,
with the first example from Spark's documentation and the value Apache Spark 3.5 gave for it) down
that same path and gives each function a status:

    runs        it gives Spark's value for the example (its value check)
    differs     it runs and gives another value: check results that use it on the warehouse
    refused     this PC refuses it by name, with a message, so nothing runs with another answer
    fails       it stops with an error that isn't a refusal
    not checked it has no example whose value can be compared (random values, the clock, the session)

Databricks SQL's built-ins are Spark's with Databricks' own added, so this is the shared part;
the values are Spark 3.5's with ANSI mode off, and every example here answers the same with it on.
``docs/databricks-functions.md`` is ``markdown()``'s output, and tests/test_databricks_coverage.py
fails when it is stale.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import spark_coverage

STATUSES = spark_coverage.STATUSES

# Where Databricks' reference gives another value than Spark 3.5 for the same example, Databricks' is
# the one checked: schema_of_json quotes field names in Databricks (sql-ref-functions-builtin, and
# engines.databricks_sql_parity's case for it).
DATABRICKS_VALUES: dict[str, list[list[Any]]] = {
    "schema_of_json": [["ARRAY<STRUCT<`col`: BIGINT>>"]],
}


def canonical(value: Any, kind: Any) -> Any:
    """A DuckDB value of type ``kind`` as spark_coverage compares a Spark one: a STRUCT keeps its fields
    in order, as a Spark Row does (DuckDB gives a STRUCT and a MAP both as a dict), and the rest as
    spark_coverage.canonical has it."""
    if value is None:
        return None
    children = dict(kind.children) if kind.id in ("struct", "list", "map") else {}
    if kind.id == "struct":
        return {"struct": [[k, canonical(v, children[k])] for k, v in value.items()]}
    if kind.id == "list":
        return [canonical(v, children["child"]) for v in value]
    if kind.id == "map":
        return {"map": sorted(([canonical(k, children["key"]), canonical(v, children["value"])]
                               for k, v in value.items()), key=json.dumps)}
    return spark_coverage.canonical(value)


def run(example: str, con: Any) -> list[Any]:
    """``example``'s rows as a Databricks SQL cell gets them here, compared as spark_coverage does."""
    from . import sql
    from .engines.local_sql import _databricks
    statements = _databricks(example)
    for statement in statements[:-1]:
        con.execute(sql.duckdb_sql(statement))
    relation = con.sql(sql.duckdb_sql(statements[-1]))
    kinds = relation.types
    rows = [[canonical(v, k) for v, k in zip(row, kinds, strict=True)] for row in relation.fetchall()]
    if any(name in example for name in spark_coverage.ORDER_FREE):
        rows = [[sorted(c, key=json.dumps) if isinstance(c, list) else c for c in row] for row in rows]
    return sorted(rows, key=json.dumps)


def connect() -> Any:
    """A DuckDB connection like a Databricks SQL cell's: closed to files and extensions, in UTC."""
    import duckdb

    from .engines.local_sql import _DUCKDB_CLOSED
    con = duckdb.connect(config=dict(_DUCKDB_CLOSED))
    con.execute("SET TimeZone = 'UTC'")
    return con


def check(function: dict[str, Any], con: Any) -> tuple[str, str]:
    """``function``'s status and a short note, from running its example as a Databricks SQL cell."""
    example, expected = function.get("example"), DATABRICKS_VALUES.get(function["name"], function.get("value"))
    if not example or function["name"] in spark_coverage.VARYING or "value" not in function:
        return "not checked", ""
    try:
        got = run(example, con)
    except Exception as exc:  # noqa: BLE001  # silent-ok: every error is a status for the coverage list
        first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        return ("refused" if spark_coverage._refusal(exc) else "fails"), first[:160]
    if got == expected:
        return "runs", ""
    return "differs", f"{json.dumps(got)[:80]} here, {json.dumps(expected)[:80]} in Databricks"


def coverage() -> list[dict[str, Any]]:
    """Every built-in with its group, example and status, in name order."""
    con = connect()
    out = []
    for function in spark_coverage.load():
        status, note = check(function, con)
        out.append({"name": function["name"], "group": function.get("group") or "other",
                    "example": function.get("example") or "", "status": status, "note": note})
    return out


def markdown(rows: list[dict[str, Any]]) -> str:
    """docs/databricks-functions.md: the counts, then every function by group."""
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    lines = [
        "# Databricks SQL functions on this PC",
        "",
        "<!-- Generated by sidecar/fabric_desktop/databricks_coverage.py. Don't edit by hand: run",
        "     `python -m fabric_desktop.databricks_coverage` in sidecar/ and commit the result. -->",
        "",
        "The built-in functions Databricks SQL shares with Spark 3.5, with what a Databricks SQL cell does",
        "with each on this PC, where the DuckDB engine runs it. Each function's first example from Spark's",
        "documentation goes through the same translation as a cell and is compared with the value",
        "Databricks gives (Spark 3.5's, or Databricks' own where its reference shows another). No",
        "Databricks workspace was used. CI runs every check again, so this page changes when a function",
        "is fixed or breaks.",
        "",
        "- **Runs**: gives Databricks' value for its example.",
        "- **Differs**: runs, but gives another value. Check results that use it on the warehouse.",
        "- **Refused**: stops with a message saying why, so nothing runs with another answer.",
        "- **Fails**: stops with an error. Run the cell on a Databricks SQL warehouse.",
        "- **Not checked**: no example whose value can be compared (random values, the clock or the "
        "session, or no example).",
        "",
        "Functions only Databricks has (such as the `ai_`, `h3_` and `st_` families) aren't listed yet.",
        "One example per function is a first check, not proof: [Engines](engines.md#databricks-sql-functions-on-this-pc)",
        "lists more checks and the differences known in more detail.",
        "",
        "| Status | Functions |",
        "| --- | ---: |",
        *[f"| {s.capitalize()} | {counts[s]} |" for s in STATUSES],
        f"| **All** | **{len(rows)}** |",
        "",
    ]
    for group in sorted({r["group"] for r in rows}):
        title = spark_coverage._TITLES.get(group, group.removesuffix("_funcs").replace("_", " ").capitalize())
        lines += [f"## {title}", "", "| Function | Status | Example | Note |", "| --- | --- | --- | --- |"]
        for r in (r for r in rows if r["group"] == group):
            cells = [f"`{r['name']}`", r["status"].capitalize(), f"`{r['example']}`" if r["example"] else "",
                     r["note"]]
            lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
        lines.append("")
    return "\n".join(lines)


DOC = Path(__file__).resolve().parents[2] / "docs" / "databricks-functions.md"

if __name__ == "__main__":  # pragma: no cover - writes the docs page
    from .jsonfile import write_text
    write_text(DOC, markdown(coverage()), newline="\n")  # LF, like the committed page
    print(f"Wrote {DOC}")

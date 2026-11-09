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

Databricks SQL's built-ins are Spark's with Databricks' own added. ``databricks_builtins.json`` lists
Databricks' own (from its alphabetical list of built-in functions, sql-ref-functions-builtin-alpha),
leaving out those that need a Databricks service: AI, geospatial (h3_, st_), files, streams, secrets,
Unity Catalog and account functions. One with an example from Databricks' reference has its value
checked as above (the value Databricks documents). Any other is tried with NULL arguments (0 to 4 of
them), or with a listed probe when it has its own syntax: "not checked" means only that DuckDB accepts
it. For the shared ones, the
values are Spark 3.5's with ANSI mode off. Databricks SQL warehouses run with ANSI mode on, which
changes overflow and bad-input cases rather than these examples, but that isn't checked here.
``docs/databricks-functions.md`` is ``markdown()``'s output, and tests/test_databricks_coverage.py
fails when it is stale.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import spark_coverage

STATUSES = spark_coverage.STATUSES
DATA = Path(__file__).with_name("databricks_builtins.json")
_ARITIES = 5

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
    from . import databricks_bytes, databricks_ip, databricks_variant
    databricks_ip.register(con)  # as engines.local_sql's DuckDB session does
    databricks_bytes.register(con)
    databricks_variant.register(con)
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


def load() -> list[dict[str, Any]]:
    """Databricks' own built-ins (not in Spark 3.5), each with its group, and an example and the value
    Databricks' reference documents for it where it has a value check."""
    return list(json.loads(DATA.read_text(encoding="utf-8")))


def probe(function: dict[str, Any], con: Any) -> tuple[str, str]:
    """The status of a function with no value check, run as a Databricks SQL cell: its listed ``probe``
    when it has its own syntax (try_cast(x AS t), CUBE, WITHIN GROUP, a unit keyword), otherwise its name
    with 0 to 4 NULL arguments. "not checked" when DuckDB accepts one, refused when this PC refuses it by
    name, and fails otherwise."""
    name = function["name"]
    tries = [function["probe"]] if function.get("probe") else [
        f"SELECT {name}({', '.join(['NULL'] * n)})" for n in range(_ARITIES)]
    errors: list[BaseException] = []
    for code in tries:
        try:
            run(code, con)
        except Exception as exc:  # noqa: BLE001  # silent-ok: every error is a status for the coverage list
            errors.append(exc)
            continue
        return "not checked", ""
    refusal = next((e for e in errors if spark_coverage._refusal(e)), None)
    if refusal is not None:
        return "refused", (str(refusal).strip().splitlines() or [""])[0][:160]
    # sqlglot names one of several missing arguments, not always the same one, so it isn't named here
    notes = [re.sub(r"Required keyword: '\w+' missing", "an argument is missing",
                    (str(e).strip().splitlines() or [type(e).__name__])[0]) for e in errors]
    other = [n for n in notes if function.get("probe") or "does not exist" not in n]
    return "fails", (other[0][:160] if other else f"DuckDB has no {name}, and the translation doesn't write it")


def coverage() -> list[dict[str, Any]]:
    """Every built-in with its group, example and status: Spark's in name order, then Databricks' own."""
    con = connect()
    out = []
    for function in spark_coverage.load():
        status, note = check(function, con)
        out.append({"name": function["name"], "group": function.get("group") or "other",
                    "example": function.get("example") or "", "status": status, "note": note})
    for function in load():
        status, note = check(function, con) if "value" in function else probe(function, con)
        note = note or function.get("note", "")  # a known difference the value check doesn't show
        out.append({"name": function["name"], "group": function["group"], "example": function.get("example", ""),
                    "status": status, "note": note})
    return out


_TITLES = {"databricks:aggregate": "Databricks only: aggregates and sketches",
           "databricks:conditional": "Databricks only: conditional and math",
           "databricks:datetime": "Databricks only: date and time", "databricks:ip": "Databricks only: IP addresses",
           "databricks:misc": "Databricks only: miscellaneous", "databricks:string": "Databricks only: string and binary",
           "databricks:variant": "Databricks only: VARIANT and JSON", "databricks:vector": "Databricks only: vectors",
           "databricks:xml": "Databricks only: XML"}


def markdown(rows: list[dict[str, Any]]) -> str:
    """docs/databricks-functions.md: the counts, then every function by group."""
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    lines = [
        "# Databricks SQL functions on this PC",
        "",
        "<!-- Generated by sidecar/fabric_desktop/databricks_coverage.py. Don't edit by hand: run",
        "     `python -m fabric_desktop.databricks_coverage` in sidecar/ and commit the result. -->",
        "",
        "Databricks SQL's built-in functions, with what a Databricks SQL cell does with each on this PC,",
        "where the DuckDB engine runs it. For the functions Databricks SQL shares with Spark 3.5, each",
        "one's first example from Spark's documentation goes through the same translation as a cell and",
        "is compared with the value Databricks gives (Spark 3.5's, or Databricks' own where its reference",
        "shows another). Databricks' own functions are in the groups headed \"Databricks only\": one with",
        "an example is compared with the value Databricks' reference documents for it, and any other is",
        "tried with NULL arguments, or a sample of its own syntax (CUBE, WITHIN GROUP, a unit keyword).",
        "No Databricks workspace was used. CI runs every check again, so this page changes when a",
        "function is fixed or breaks.",
        "",
        "- **Runs**: gives Databricks' value for its example.",
        "- **Differs**: runs, but gives another value. Check results that use it on the warehouse.",
        "- **Refused**: stops with a message saying why, so nothing runs with another answer.",
        "- **Fails**: stops with an error. Run the cell on a Databricks SQL warehouse.",
        "- **Not checked**: no example whose value can be compared (random values, the clock or the "
        "session, or no example), or DuckDB accepts it but no check compares its value yet.",
        "",
        "AI (`ai_`), geospatial (`h3_`, `st_`), file, stream, secret, Unity Catalog and account functions",
        "aren't listed: they need Databricks' own services. The IP address functions (`ip_`), `url_decode`,",
        "`make_valid_utf8`, `hmac`, `bit_reverse` and the VARIANT ones (`parse_json`, `variant_get`, ...) run in",
        "Python, one row at a time, so a query that calls them on millions of rows takes seconds longer here.",
        "",
        "One example per function is a first check, not proof: [Engines](engines.md#databricks-sql-functions-on-this-pc)",
        "lists more checks and the differences known in more detail.",
        "",
        "| Status | Functions |",
        "| --- | ---: |",
        *[f"| {s.capitalize()} | {counts[s]} |" for s in STATUSES],
        f"| **All** | **{len(rows)}** |",
        "",
    ]
    for group in sorted({r["group"] for r in rows}, key=lambda g: (g.startswith("databricks:"), g)):
        title = _TITLES.get(group) or spark_coverage._TITLES.get(
            group, group.removesuffix("_funcs").replace("_", " ").capitalize())
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

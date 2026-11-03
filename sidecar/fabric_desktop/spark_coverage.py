"""Which Spark SQL built-in functions answer as Spark does in a notebook on this PC (#1668).

``spark_builtins.json`` lists every built-in function of Spark 3.5 (the Spark of Fabric runtime 1.3),
with the first example from Spark's own documentation of it and the value Apache Spark 3.5 gave for
that example. ``tools/capture_spark_builtins.py`` makes that file with a real PySpark and Java; it
is run by hand when the Spark version changes, never in CI.

``coverage()`` runs each example through this PC's Spark (LocalSparkSession.sql, the path a notebook
cell takes) and gives each function a status:

    runs        it runs and gives Spark's value for the example (its value check)
    differs     it runs and gives another value: check results that use it in Fabric
    refused     this PC's Spark refuses it by name, with a message, so nothing runs with another answer
    fails       it stops with an error that isn't a refusal
    not checked it has no example whose value can be compared (no example, or its value changes
                from run to run: random numbers, the time, the session)

A function is "runs" only when its value check passes, so every function marked as running has one.
``docs/spark-functions.md`` is ``markdown()``'s output; tests/test_spark_coverage.py fails when it is
stale, so an engine, sqlglot or DuckDB change that fixes or breaks a function shows up there.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

DATA = Path(__file__).with_name("spark_builtins.json")
STATUSES = ("runs", "differs", "refused", "fails", "not checked")

# Functions whose example's value changes from run to run or machine to machine, so no value check:
# random values, the clock, the session and the file or partition a row came from.
VARYING = frozenset({
    "aes_encrypt", "current_catalog", "current_database", "current_date", "current_schema", "current_timestamp",
    "current_timezone", "current_user", "curdate", "input_file_block_length", "input_file_block_start",
    "input_file_name", "localtimestamp", "monotonically_increasing_id", "now", "rand", "randn", "random",
    "reflect", "java_method", "session_user", "shuffle", "spark_partition_id", "user", "uuid", "version",
    "try_reflect", "unix_timestamp", "uniform", "randstr",
})


def canonical(value: Any) -> Any:
    """``value`` (a cell of a collected row, from Spark or from this PC) as plain JSON, so the two
    compare: decimals by their digits, floats to 12 significant digits, dates and times as ISO text,
    bytes as hex, rows and maps as name/value pairs."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "float:nan"
        if math.isinf(value):
            return "float:inf" if value > 0 else "float:-inf"
        return f"float:{value:.12g}"
    if isinstance(value, Decimal):
        return f"decimal:{value}"
    if isinstance(value, dt.datetime):
        return f"timestamp:{value.replace(tzinfo=None).isoformat()}"
    if isinstance(value, dt.date):
        return f"date:{value.isoformat()}"
    if isinstance(value, dt.time):
        return f"time:{value.isoformat()}"
    if isinstance(value, dt.timedelta):
        return f"interval:{value.total_seconds()}"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"binary:{bytes(value).hex()}"
    if isinstance(value, dict):
        return {"map": sorted(([canonical(k), canonical(v)] for k, v in value.items()), key=json.dumps)}
    if hasattr(value, "asDict"):  # a Row (a struct)
        return {"struct": [[k, canonical(v)] for k, v in value.asDict(recursive=False).items()]}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    return f"{type(value).__name__}:{value}"


# Functions whose result has no set order in Spark either, so their lists are compared sorted.
ORDER_FREE = frozenset({"collect_set"})


def rows_value(rows: list[Any], name: str = "") -> list[Any]:
    """An example's rows as compared: every row, canonical, in sorted order (an example without
    ORDER BY returns its rows in any order)."""
    out = [canonical(list(r)) for r in rows]
    if name in ORDER_FREE:
        out = [[sorted(c, key=json.dumps) if isinstance(c, list) else c for c in row] for row in out]
    return sorted(out, key=json.dumps)


def load() -> list[dict[str, Any]]:
    return list(json.loads(DATA.read_text(encoding="utf-8")))


def _refusal(exc: BaseException) -> bool:
    """Whether an error is this PC refusing the function with its own message ("can't run on this PC
    the way it runs in Fabric, so nothing ran"), rather than DuckDB failing on it."""
    import duckdb
    return isinstance(exc, NotImplementedError) or (not isinstance(exc, duckdb.Error) and "this pc" in str(exc).lower())


def check(function: dict[str, Any], spark: Any) -> tuple[str, str]:
    """``function``'s status (see the module docstring) and a short note, by running its example on
    ``spark`` (this PC's Spark session)."""
    example, expected = function.get("example"), function.get("value")
    if not example or function["name"] in VARYING or "value" not in function:
        return "not checked", ""
    try:
        got = rows_value(spark.sql(example).collect(), function["name"])
    except Exception as exc:  # noqa: BLE001  # silent-ok: every error is a status for the coverage list
        first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        return ("refused" if _refusal(exc) else "fails"), first[:160]
    if got == expected:
        return "runs", ""
    return "differs", f"{json.dumps(got)[:80]} here, {json.dumps(expected)[:80]} in Spark"


def coverage() -> list[dict[str, Any]]:
    """Every built-in with its group, example and status, in name order."""
    from .notebook import Kernel  # this PC's Spark, as a notebook cell gets it
    kernel = Kernel()
    spark = kernel.namespace["spark"]
    out = []
    for function in load():
        status, note = check(function, spark)
        out.append({"name": function["name"], "group": function.get("group") or "other",
                    "example": function.get("example") or "", "status": status, "note": note})
    return out


_TITLES = {"agg_funcs": "Aggregate", "array_funcs": "Array", "bitwise_funcs": "Bitwise", "collection_funcs": "Collection",
           "conditional_funcs": "Conditional", "conversion_funcs": "Conversion", "csv_funcs": "CSV",
           "datetime_funcs": "Date and time", "generator_funcs": "Generator", "hash_funcs": "Hash",
           "json_funcs": "JSON", "lambda_funcs": "Lambda", "map_funcs": "Map", "math_funcs": "Math",
           "misc_funcs": "Miscellaneous", "other": "Operators and other", "predicate_funcs": "Predicate",
           "string_funcs": "String", "struct_funcs": "Struct", "url_funcs": "URL", "window_funcs": "Window",
           "xml_funcs": "XML"}


def markdown(rows: list[dict[str, Any]]) -> str:
    """docs/spark-functions.md: the counts, then every function by group."""
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    lines = [
        "# Spark functions on this PC",
        "",
        "<!-- Generated by sidecar/fabric_desktop/spark_coverage.py. Don't edit by hand: run",
        "     `python -m fabric_desktop.spark_coverage` in sidecar/ and commit the result. -->",
        "",
        "Every built-in function of Spark 3.5, the Spark of Fabric runtime 1.3, with what a notebook's",
        "Spark SQL does with it on this PC. Each function's first example from Spark's documentation is",
        "run here and compared with the value Apache Spark 3.5 gives, on the DuckDB version the app ships.",
        "CI runs every check again, so this page changes when a function is fixed or breaks.",
        "",
        "- **Runs**: gives Spark's value for its example.",
        "- **Differs**: runs, but gives another value. Check results that use it in Fabric.",
        "- **Refused**: stops with a message saying why, so nothing runs with another answer.",
        "- **Fails**: stops with an error. Run the cell in Fabric.",
        "- **Not checked**: no example whose value can be compared (random values, the clock or the "
        "session, or no example).",
        "",
        "One example per function is a first check, not proof: a function can match on its example and",
        "differ on other input. [Engines](engines.md) lists the differences known in more detail.",
        "",
        "| Status | Functions |",
        "| --- | ---: |",
        *[f"| {s.capitalize()} | {counts[s]} |" for s in STATUSES],
        f"| **All** | **{len(rows)}** |",
        "",
    ]
    for group in sorted({r["group"] for r in rows}):
        title = _TITLES.get(group, group.removesuffix("_funcs").replace("_", " ").capitalize())
        lines += [f"## {title}", "", "| Function | Status | Example | Note |", "| --- | --- | --- | --- |"]
        for r in (r for r in rows if r["group"] == group):
            cells = [f"`{r['name']}`", r["status"].capitalize(), f"`{r['example']}`" if r["example"] else "",
                     r["note"]]
            lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
        lines.append("")
    return "\n".join(lines)


DOC = Path(__file__).resolve().parents[2] / "docs" / "spark-functions.md"

if __name__ == "__main__":  # pragma: no cover - writes the docs page
    DOC.write_text(markdown(coverage()), encoding="utf-8")
    print(f"Wrote {DOC}")

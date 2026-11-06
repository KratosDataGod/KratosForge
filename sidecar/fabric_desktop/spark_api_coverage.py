"""Which PySpark DataFrame API methods answer as Spark does in a notebook on this PC.

``spark_dataframe_api.json`` lists every public method and property of PySpark 3.5's DataFrame,
Column, GroupedData, DataFrameReader, DataFrameWriter, DataFrameNaFunctions and
DataFrameStatFunctions (the Spark of Fabric runtime 1.3), each with the examples from its own
PySpark documentation and what each example gave in Apache Spark 3.5: the value of an expression
(a DataFrame by its columns and rows) and what it printed. ``tools/capture_spark_dataframe_api.py``
makes that file with a real PySpark and Java; it is run by hand when the Spark version changes, never
in CI.

``coverage()`` runs each method's examples, in order, as notebook cells on this PC (Kernel.run, the
path a notebook cell takes) and gives the method a status, as spark_coverage does for SQL functions:

    runs        every example runs and gives what Spark gave
    differs     the examples run, but one gives another value or prints something else
    refused     this PC refuses it by name, with a message, so nothing runs with another answer
    fails       an example stops with an error that isn't a refusal
    not checked no example whose result can be compared (none in Spark's documentation, Spark
                itself stopped on it, or its result changes from run to run)

``docs/spark-dataframe-api.md`` is ``markdown()``'s output; tests/test_spark_api_coverage.py fails
when it is stale.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:  # the capture tool loads this file on its own, next to spark_coverage
    from .spark_coverage import STATUSES, canonical
except ImportError:  # pragma: no cover - only in tools/capture_spark_dataframe_api.py
    from spark_coverage import STATUSES, canonical  # type: ignore[import-not-found,no-redef]

DATA = Path(__file__).with_name("spark_dataframe_api.json")
CLASSES = ("DataFrame", "Column", "GroupedData", "DataFrameReader", "DataFrameWriter", "DataFrameNaFunctions",
           "DataFrameStatFunctions")
VALUE = "kf_api_value"  # the name an example's value is kept under while it is compared


# What an API object is, told by what it can do (this PC's classes have other names): checked in order.
_ROLES = (
    ("RDD", ("mapPartitions", "reduceByKey")),
    ("DataFrame", ("collect", "columns", "select")),
    ("DataFrameWriter", ("save", "mode", "saveAsTable")),
    ("DataStreamWriter", ("start", "trigger")),
    ("DataFrameReader", ("load", "option", "table")),
    ("GroupedData", ("agg", "pivot")),
    ("DataFrameNaFunctions", ("fill", "replace", "drop")),
    ("DataFrameStatFunctions", ("corr", "cov")),
    ("SparkSession", ("sql", "createDataFrame")),
    ("Column", ("alias", "cast", "isNull")),
)


def _role(obj: Any) -> str | None:
    for role, needs in _ROLES:
        if all(hasattr(obj, n) for n in needs):
            return role
    return None


def outcome(value: Any, printed: str) -> dict[str, Any]:
    """What an example gave, as plain JSON that compares between Spark and this PC: a DataFrame by
    its columns and rows (in sorted order: an example without orderBy has no set order), a schema by
    its simple string, another API object (or its class) by what it is, not its class's name, which
    differs here; anything else as spark_coverage.canonical gives it; and the text it printed,
    without trailing spaces."""
    out: dict[str, Any] = {}
    text = "\n".join(line.rstrip() for line in printed.strip("\n").splitlines())
    if text:
        out["printed"] = text
    if value is None:
        return out
    role = _role(value)
    if isinstance(value, type):
        out["value"] = {"class": role or value.__name__}
    elif role == "DataFrame":
        rows = [canonical(list(r)) for r in value.collect()]
        out["value"] = {"columns": list(value.columns), "rows": sorted(rows, key=json.dumps)}
    elif hasattr(value, "simpleString"):
        out["value"] = {"type": value.simpleString()}
    elif role is not None:
        out["value"] = {"kind": role}
    else:
        out["value"] = canonical(value)
    return out


def load() -> list[dict[str, Any]]:
    return list(json.loads(DATA.read_text(encoding="utf-8")))


def _refusal(exc_text: str) -> bool:
    lowered = exc_text.lower()
    return "notimplementederror" in lowered or "this pc" in lowered


def _run(kernel: Any, source: str) -> tuple[dict[str, Any] | None, str]:
    """One example as a notebook cell: what it gave (None when it stopped), or the error's first line."""
    expression = _expression(source)
    code = f"{VALUE} = ({source})" if expression else source
    kernel.namespace.pop(VALUE, None)
    result = kernel.run(code)
    printed = "".join(o.get("text", "") for o in result["outputs"] if o.get("type") in ("text", "stream"))
    if result["status"] != "ok":
        error: dict[str, Any] = next((o for o in result["outputs"] if o.get("type") == "error"), {})
        text = f"{error.get('ename', '')}: {error.get('evalue', '')}".strip(": ") or result["status"]
        return None, _TEMP.sub("<temp dir>", (text.splitlines() or [text])[0])[:160]
    try:
        return outcome(kernel.namespace.pop(VALUE, None) if expression else None, printed), ""
    except Exception as exc:  # noqa: BLE001  # silent-ok: a value this PC can't collect is a status
        return None, f"{type(exc).__name__}: {exc}".splitlines()[0][:160]


# A temporary directory an example made (tempfile's tmpXXXXXXXX), named the same in every run.
_TEMP = re.compile(r"[^\s\"']*[\\/]tmp[a-z0-9_]{8}\b")


def _expression(source: str) -> bool:
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr)


def check(method: dict[str, Any], kernel: Any) -> tuple[str, str]:
    """``method``'s status and a short note, by running its examples on ``kernel``."""
    examples = method.get("examples") or []
    if not examples or method.get("varying") or method.get("spark_error"):
        return "not checked", ""
    for example in examples:
        got, error = _run(kernel, example["source"])
        want = example.get("outcome")
        if want is None:  # Spark stopped on this one, as its documentation shows: so must this PC
            if got is not None:
                return "differs", f"`{_short(example['source'])}` runs here; Spark stops on it"
            continue
        if got is None:
            return ("refused" if _refusal(error) else "fails"), f"`{_short(example['source'])}`: {error}"
        if got != want:
            return "differs", (f"`{_short(example['source'])}`: {json.dumps(got)[:70]} here, "
                               f"{json.dumps(want)[:70]} in Spark")
    return "runs", ""


def _short(source: str) -> str:
    line = " ".join(source.split())
    return line if len(line) <= 60 else line[:57] + "..."


def coverage() -> list[dict[str, Any]]:
    """Every method with its class and status, in the data file's order. Each class's methods share
    one notebook kernel, as one notebook's cells would, so an example can use what an earlier one made."""
    from .notebook import Kernel
    out = []
    kernels: dict[str, Any] = {}
    for method in load():
        kernel = kernels.get(method["class"]) or kernels.setdefault(method["class"], Kernel())
        status, note = check(method, kernel)
        out.append({"class": method["class"], "name": method["name"], "status": status, "note": note})
    return out


def markdown(rows: list[dict[str, Any]]) -> str:
    """docs/spark-dataframe-api.md: the counts, then every method by class."""
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    lines = [
        "# PySpark DataFrame API on this PC",
        "",
        "<!-- Generated by sidecar/fabric_desktop/spark_api_coverage.py. Don't edit by hand: run",
        "     `python -m fabric_desktop.spark_api_coverage` in sidecar/ and commit the result. -->",
        "",
        "Every public method of PySpark 3.5's DataFrame, Column, GroupedData, reader, writer, `na` and",
        "`stat`, the Spark of Fabric runtime 1.3, with what a notebook does with it on this PC. Each",
        "method's examples from PySpark's documentation are run here as notebook cells and compared with",
        "what Apache Spark 3.5 gives: each value (a DataFrame by its columns and rows) and what it prints.",
        "CI runs every check again, so this page changes when a method is fixed or breaks.",
        "",
        "- **Runs**: every example gives what Spark gives.",
        "- **Differs**: runs, but an example gives another value or prints something else.",
        "- **Refused**: stops with a message saying why, so nothing runs with another answer.",
        "- **Fails**: an example stops with an error. Run the cell in Fabric.",
        "- **Not checked**: no example whose result can be compared (none documented, Spark itself "
        "stops on it, or its result changes from run to run).",
        "",
        "Documented examples are a first check, not proof: a method can match on them and differ on other",
        "input. [Spark functions](spark-functions.md) lists the SQL functions the same way.",
        "",
        "| Status | Methods |",
        "| --- | ---: |",
        *[f"| {s.capitalize()} | {counts[s]} |" for s in STATUSES],
        f"| **All** | **{len(rows)}** |",
        "",
    ]
    for cls in CLASSES:
        members = [r for r in rows if r["class"] == cls]
        if not members:
            continue
        lines += [f"## {cls}", "", "| Method | Status | Note |", "| --- | --- | --- |"]
        for r in members:
            cells = [f"`{r['name']}`", r["status"].capitalize(), r["note"]]
            lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
        lines.append("")
    return "\n".join(lines)


DOC = Path(__file__).resolve().parents[2] / "docs" / "spark-dataframe-api.md"

if __name__ == "__main__":  # pragma: no cover - writes the docs page
    from .jsonfile import write_text
    write_text(DOC, markdown(coverage()), newline="\n")
    print(f"Wrote {DOC}")

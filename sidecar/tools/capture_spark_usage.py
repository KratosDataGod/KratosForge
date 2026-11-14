"""Capture Spark's own usage line for each built-in function, for fabric_desktop/spark_function_help.json.

The SQL editor's hover card and signature help (editor parity S07/S11) show it for Spark SQL. Needs
PySpark 3.5 and Java, and is run by hand, never in CI, when the Spark version KratosForge emulates
changes (spark_api.SPARK_VERSION), as tools/capture_spark_builtins.py is:

    python -m venv spark && spark/bin/pip install pyspark==3.5.5
    spark/bin/python tools/capture_spark_usage.py

For each function in SHOW SYSTEM FUNCTIONS it keeps the forms from ExpressionInfo.getUsage(), each as
"name(arguments)" and its first sentence, and the arguments text when Spark documents one. Spark's
documentation is Apache License 2.0 (NOTICE).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pyspark.sql import SparkSession

OUT = Path(__file__).resolve().parents[1] / "fabric_desktop" / "spark_function_help.json"


def forms(usage: str, name: str) -> list[list[str]]:
    """[[signature, summary], ...] from a usage text: one per line that starts with the name (or _FUNC_)."""
    form = re.compile(rf"^\s*(?:_FUNC_|{re.escape(name)})(\([^\n]*?\))?\s+-\s+(.*)$", re.IGNORECASE)
    out: list[list[str]] = []
    for line in usage.splitlines():
        m = form.match(line)
        if not m:
            if out and line.strip():
                out[-1][1] = f"{out[-1][1]} {line.strip()}"
            continue
        args = (m.group(1) or "()").strip()
        out.append([f"{name}{args}", m.group(2).strip()])
    for f in out:
        text = re.sub(r"\s+", " ", f[1].replace("_FUNC_", name))
        first = re.match(r"(.+?\.)(\s|$)", text)
        f[1] = (first.group(1) if first else text)[:300]
    unique: list[list[str]] = []
    for f in out:
        if f not in unique:
            unique.append(f)
    return unique


def main() -> None:
    spark = SparkSession.builder.master("local[1]").config("spark.ui.enabled", "false").getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    catalog = spark._jsparkSession.sessionState().catalog()
    result: dict = {"_source": f"Apache Spark {spark.version} ExpressionInfo usage (Apache License 2.0), "
                               "captured by tools/capture_spark_usage.py"}
    for name in sorted({r[0] for r in spark.sql("SHOW SYSTEM FUNCTIONS").collect()}):
        if not re.match(r"^[a-z_][a-z0-9_]*$", name):
            continue  # operators: no call to help with
        found = catalog.lookupBuiltinOrTempFunction(name)
        if found.isEmpty():
            continue
        info = found.get()
        usage = (info.getUsage() or "").strip()
        got = forms(usage, name)
        if got:
            result[name] = got
    OUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(result) - 1} functions -> {OUT}")
    spark.stop()


if __name__ == "__main__":
    main()

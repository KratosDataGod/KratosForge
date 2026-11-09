"""Capture Spark's built-in functions for fabric_desktop/spark_builtins.json (#1668).

Needs PySpark 3.5 and Java, and is run by hand, never in CI, when the Spark version KratosForge
emulates changes (spark_api.SPARK_VERSION):

    python -m venv spark && spark/bin/pip install pyspark==3.5.5
    spark/bin/python tools/capture_spark_builtins.py

For each function in SHOW SYSTEM FUNCTIONS it keeps the group and the first example from Spark's own
documentation of it, runs that example in Spark (UTC session), and keeps its rows as
spark_coverage.rows_value gives them. An example that fails in Spark is left without a value.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "fabric_desktop"
spec = importlib.util.spec_from_file_location("spark_coverage", HERE / "spark_coverage.py")
coverage = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(coverage)  # type: ignore[union-attr]  # no engine imports needed

from pyspark.sql import SparkSession  # noqa: E402

spark = (SparkSession.builder.master("local[1]").config("spark.ui.enabled", "false")
         .config("spark.sql.session.timeZone", "UTC").getOrCreate())
spark.sparkContext.setLogLevel("ERROR")
catalog = spark._jsparkSession.sessionState().catalog()
out = []
for name in sorted({r[0] for r in spark.sql("SHOW SYSTEM FUNCTIONS").collect()}):
    found = catalog.lookupBuiltinOrTempFunction(name)
    info = None if found.isEmpty() else found.get()
    entry: dict = {"name": name, "group": (info.getGroup() if info else "") or "other"}
    examples = re.findall(r">\s*(SELECT .*?);", (info.getExamples() or "") if info else "", re.S)
    if examples:
        entry["example"] = " ".join(examples[0].split())
        try:
            entry["value"] = coverage.rows_value(spark.sql(entry["example"]).collect(), name)
        except Exception as exc:  # noqa: BLE001 - an example Spark itself refuses has no value
            entry["spark_error"] = str(exc).strip().splitlines()[0][:160]
    out.append(entry)
(HERE / "spark_builtins.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"{len(out)} functions, Spark {spark.version}")

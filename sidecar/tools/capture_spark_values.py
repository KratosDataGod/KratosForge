"""Capture Spark 3.5's answers to hand-written queries, one fixture per case file, for the value tests.

Each line of tools/spark_value_cases/<name>.txt is one query (lines starting with # are notes). Spark
answers each, and tests/fixtures/spark_3_5_3_<name>.json keeps the rows as spark_coverage.rows_value
gives them, or the first line of Spark's error. A test then asks this PC's Spark session (a notebook's
spark.sql) the same query.

Needs PySpark 3.5 and Java, and is run by hand, never in CI (as tools/capture_spark_builtins.py is):

    python -m venv spark && spark/bin/pip install pyspark==3.5.3
    spark/bin/python tools/capture_spark_values.py json [hashing ...]

Spark runs in local mode, UTC, ANSI mode off: how Fabric runs Spark SQL (docs/sql-compatibility.md).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = Path(__file__).resolve().with_name("spark_value_cases")
spec = importlib.util.spec_from_file_location("spark_coverage", ROOT / "fabric_desktop" / "spark_coverage.py")
coverage = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(coverage)  # type: ignore[union-attr]  # no engine imports needed


def queries(name: str) -> list[str]:
    lines = (CASES / f"{name}.txt").read_text(encoding="utf-8").splitlines()
    return list(dict.fromkeys(q.strip() for q in lines if q.strip() and not q.lstrip().startswith("#")))


def main(names: list[str]) -> None:
    from pyspark.sql import SparkSession

    spark = (SparkSession.builder.master("local[1]").config("spark.ui.enabled", "false")
             .config("spark.sql.session.timeZone", "UTC").config("spark.sql.ansi.enabled", "false").getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    for name in names:
        cases = []
        for q in queries(name):
            try:
                cases.append({"sql": q, "value": coverage.rows_value(spark.sql(q).collect())})
            except Exception as exc:  # noqa: BLE001 - silent-ok: Spark's own refusal is the recorded answer
                cases.append({"sql": q, "error": (str(exc).strip().splitlines() or [type(exc).__name__])[0][:300]})
        out = ROOT / "tests" / "fixtures" / f"spark_3_5_3_{name}.json"
        out.write_text(json.dumps({"spark": spark.version, "ansi": False, "time_zone": "UTC", "cases": cases},
                                  indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{name}: {len(cases)} cases, {sum('error' in c for c in cases)} Spark errors -> {out}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["json"])

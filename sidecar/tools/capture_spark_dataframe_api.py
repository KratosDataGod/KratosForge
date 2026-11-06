"""Capture PySpark's DataFrame API for fabric_desktop/spark_dataframe_api.json.

Needs PySpark 3.5 and Java, and is run by hand, never in CI, when the Spark version KratosForge
emulates changes (spark_api.SPARK_VERSION):

    python -m venv spark && spark/bin/pip install pyspark==3.5.3
    spark/bin/python tools/capture_spark_dataframe_api.py

For each public method and property of the classes in spark_api_coverage.CLASSES it keeps the
examples from its PySpark documentation (doctest examples, those marked +SKIP left out) and runs
them in order in Spark (UTC session), one namespace per class as one notebook's cells would share
one. Each example keeps what spark_api_coverage.outcome gives for it; an example whose documentation
shows Spark stopping keeps no outcome. The whole run is made twice, each in a new Spark session,
and a method whose examples give something else the second time is marked varying.
"""

from __future__ import annotations

import contextlib
import doctest
import importlib.util
import inspect
import io
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parents[1] / "fabric_desktop"
sys.path.insert(0, str(HERE))  # spark_api_coverage imports spark_coverage beside it, no engine imports
spec = importlib.util.spec_from_file_location("spark_api_coverage", HERE / "spark_api_coverage.py")
api = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(api)  # type: ignore[union-attr]

import pyspark.sql  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402

SKIP = doctest.OPTIONFLAGS_BY_NAME["SKIP"]


def examples(cls: type, name: str) -> list[doctest.Example]:
    doc = inspect.getdoc(getattr(cls, name)) or ""
    return [e for e in doctest.DocTestParser().get_examples(doc) if not e.options.get(SKIP)]


def run_once() -> list[dict[str, Any]]:
    spark = (SparkSession.builder.master("local[1]").config("spark.ui.enabled", "false")
             .config("spark.sql.session.timeZone", "UTC").getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    out = []
    for cls_name in api.CLASSES:
        cls = getattr(pyspark.sql, cls_name)
        namespace: dict[str, Any] = {"spark": spark}
        for name in sorted(n for n in dir(cls) if not n.startswith("_")):
            entry: dict[str, Any] = {"class": cls_name, "name": name, "examples": []}
            for example in examples(cls, name):
                source = example.source.strip()
                record: dict[str, Any] = {"source": source}
                printed = io.StringIO()
                try:
                    with contextlib.redirect_stdout(printed):
                        if api._expression(source):
                            value = eval(compile(source, "<example>", "eval"), namespace)  # noqa: S307
                        else:
                            exec(compile(source, "<example>", "exec"), namespace)  # noqa: S102
                            value = None
                        record["outcome"] = api.outcome(value, printed.getvalue())
                except Exception as exc:  # noqa: BLE001  # Spark stopping is recorded, as documented or not
                    if example.exc_msg is None:
                        entry["spark_error"] = f"{type(exc).__name__}: {exc}".splitlines()[0][:160]
                        break
                entry["examples"].append(record)
            out.append(entry)
    spark.stop()
    return out


first = run_once()
second = run_once()
for a, b in zip(first, second, strict=True):
    if a != b:
        a["varying"] = True
(HERE / "spark_dataframe_api.json").write_text(json.dumps(first, indent=1, ensure_ascii=False) + "\n",
                                               encoding="utf-8")
print(f"{len(first)} methods, Spark {pyspark.__version__}")

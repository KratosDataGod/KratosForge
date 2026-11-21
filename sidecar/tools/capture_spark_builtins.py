"""Capture Spark's built-in functions for fabric_desktop/spark_builtins.json (#1668).

Needs PySpark 3.5 and Java, and is run by hand, never in CI, when the Spark version KratosForge
emulates changes (spark_api.SPARK_VERSION):

    python -m venv spark && spark/bin/pip install pyspark==3.5.5
    spark/bin/python tools/capture_spark_builtins.py

For each function in SHOW SYSTEM FUNCTIONS it keeps the group and the first example from Spark's own
documentation of it, runs that example in Spark (UTC session), and keeps its rows as
spark_coverage.rows_value gives them. An example that fails in Spark is left without a value.

Spark documents no example for its cast functions and operators, and its regexp and make_interval
examples fail in Spark itself (the regexp pattern's backslashes; an interval can't be collected to
Python). OWN_EXAMPLES gives those an example of our own, so they get a value check too.
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

OWN_EXAMPLES = {
    "!=": "SELECT 1 != 2, 'a' != 'a', NULL != 1",
    "<>": "SELECT 1 <> 2, 'a' <> 'a', NULL <> 1",
    "between": "SELECT 2 BETWEEN 1 AND 3, 4 BETWEEN 1 AND 3, 'b' BETWEEN 'a' AND 'c'",
    "bigint": "SELECT bigint('12'), bigint(3.9), bigint(-3.9)",
    "binary": "SELECT binary('Spark')",
    "boolean": "SELECT boolean('true'), boolean(0), boolean('no')",
    "case": "SELECT CASE WHEN 1 > 0 THEN 'a' ELSE 'b' END, CASE 2 WHEN 1 THEN 'x' END",
    "date": "SELECT date('2026-01-31'), date(TIMESTAMP '2026-01-31 23:59:59')",
    "decimal": "SELECT decimal('12.345'), decimal(7)",
    "double": "SELECT double('1.5'), double(2)",
    "float": "SELECT float('1.5'), float(2)",
    "int": "SELECT int('12'), int(3.9), int(-3.9)",
    "make_interval": "SELECT TIMESTAMP '2026-01-31 00:00:00' + make_interval(1, 2, 0, 3, 4, 5, 6.5)",
    "range": "SELECT * FROM range(1, 7, 2)",
    "regexp": "SELECT regexp('Steven', 'Ste(v|ph)en'), regexp('Stefan', 'Ste(v|ph)en')",
    "regexp_like": "SELECT regexp_like('Steven', 'Ste(v|ph)en'), regexp_like('Stefan', 'Ste(v|ph)en')",
    "rlike": "SELECT rlike('Steven', 'Ste(v|ph)en'), rlike('Stefan', 'Ste(v|ph)en')",
    "smallint": "SELECT smallint('12'), smallint(3.9)",
    "string": "SELECT string(12), string(1.50), string(true), string(DATE '2026-01-31')",
    "timestamp": "SELECT timestamp('2026-01-31 10:30:00'), timestamp(DATE '2026-01-31')",
    "tinyint": "SELECT tinyint('12'), tinyint(3.9)",
    "||": "SELECT 'Spark' || 'SQL', 'a' || NULL",
    # The clock, random numbers and the session, checked by what doesn't vary (spark_coverage.VARYING).
    "curdate": "SELECT curdate() = current_date(), typeof(curdate())",
    "current_catalog": "SELECT current_catalog()",
    "current_date": "SELECT current_date() = to_date(current_timestamp()), typeof(current_date())",
    "current_timestamp": "SELECT current_timestamp() = now(), typeof(current_timestamp())",
    "current_timezone": "SELECT current_timezone()",
    "localtimestamp": "SELECT typeof(localtimestamp())",
    "now": "SELECT now() = current_timestamp(), typeof(now())",
    "rand": "SELECT rand() >= 0 AND rand() < 1, typeof(rand())",
    "randn": "SELECT typeof(randn()), randn() IS NOT NULL",
    "random": "SELECT random() >= 0 AND random() < 1, typeof(random())",
    "shuffle": "SELECT array_sort(shuffle(array(1, 20, 3, 5))), size(shuffle(array(1, 20, NULL, 5))), shuffle(NULL)",
    "unix_timestamp": "SELECT unix_timestamp('2016-04-08', 'yyyy-MM-dd'), unix_timestamp(TIMESTAMP '2016-04-08 10:00:00')",
    "uuid": "SELECT length(uuid()), uuid() RLIKE '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', "
            "uuid() = uuid()",
}

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
    if name in OWN_EXAMPLES or examples:
        entry["example"] = OWN_EXAMPLES.get(name) or " ".join(examples[0].split())
        try:
            entry["value"] = coverage.rows_value(spark.sql(entry["example"]).collect(), name)
        except Exception as exc:  # noqa: BLE001 - an example Spark itself refuses has no value
            entry["spark_error"] = str(exc).strip().splitlines()[0][:160]
    out.append(entry)
(HERE / "spark_builtins.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"{len(out)} functions, Spark {spark.version}")

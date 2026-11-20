"""Writes the Spark-made Delta fixtures for the reader tests. Run with pyspark 3.5.5 + delta-spark 3.3.2:
python make_spark_delta_fixtures.py <out folder>

``python make_spark_delta_fixtures.py <out folder> cm_legacy`` writes only cm_legacy, a column-mapped table
with the legacy protocol (reader 2, writer 5) that Spark and Databricks made before table features, so
the other fixtures stay as they were checked in."""
import sys
from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession

b = (SparkSession.builder.master("local[1]").config("spark.ui.enabled", "false")
     .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
     .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
     .config("spark.sql.session.timeZone", "UTC"))
s = configure_spark_with_delta_pip(b).getOrCreate()
out = sys.argv[1]


def make(name, opts, part=None):
    df = s.range(0, 10).selectExpr("id", "cast(id * 10 as int) as amount", "concat('n', id) as label",
                                   "case when id < 5 then 'a' else 'b' end as p")
    w = df.coalesce(1).write.format("delta")
    for k, v in opts.items():
        w = w.option(k, v)
    if part:
        w = w.partitionBy(part)
    w.save(f"{out}/{name}")
    return f"delta.`{out}/{name}`"


if sys.argv[2:] == ["cm_legacy"]:
    t = make("cm_legacy", {"delta.minReaderVersion": "2", "delta.minWriterVersion": "5",
                           "delta.columnMapping.mode": "name"})
    s.sql(f"ALTER TABLE {t} RENAME COLUMN amount TO total")
    s.sql(f"INSERT INTO {t} VALUES (10, 100, 'n10', 'b')")
    rows = s.read.format("delta").load(f"{out}/cm_legacy").orderBy("id").collect()
    print("cm_legacy", [r.asDict() for r in rows])
    sys.exit(0)

t = make("dv", {"delta.enableDeletionVectors": "true"})
s.sql(f"DELETE FROM {t} WHERE id IN (3, 7)")
s.sql(f"DELETE FROM {t} WHERE id = 8")  # a second DV on the same file replaces the first
t = make("dv_part", {"delta.enableDeletionVectors": "true"}, part="p")
s.sql(f"DELETE FROM {t} WHERE id IN (1, 6)")
t = make("cm_name", {"delta.columnMapping.mode": "name"})
s.sql(f"ALTER TABLE {t} RENAME COLUMN amount TO total")
s.sql(f"ALTER TABLE {t} RENAME COLUMN label TO `the label`")
s.sql(f"ALTER TABLE {t} DROP COLUMN p")
t = make("cm_id", {"delta.columnMapping.mode": "id"})
s.sql(f"ALTER TABLE {t} RENAME COLUMN amount TO total")
t = make("dv_cm", {"delta.enableDeletionVectors": "true", "delta.columnMapping.mode": "name"})
s.sql(f"DELETE FROM {t} WHERE id IN (2, 5)")
s.sql(f"ALTER TABLE {t} RENAME COLUMN amount TO total")
for name in ("dv", "dv_part", "cm_name", "cm_id", "dv_cm"):
    rows = s.read.format("delta").load(f"{out}/{name}").orderBy("id").collect()
    print(name, [r.asDict() for r in rows])

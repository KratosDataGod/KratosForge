# Fabric notebook source

# PARAMETERS CELL ********************

regions = ""

# CELL ********************

from functools import reduce
from pyspark.sql import functions as F

names = [r for r in regions.split(",") if r]
if not names:
    raise ValueError("build_summary needs regions, such as 'east,west'")
loaded = reduce(lambda a, b: a.unionByName(b), [spark.read.table(f"silver.orders_{r}") for r in names])
summary = loaded.groupBy("region").agg(F.count("*").alias("orders"), F.sum("amount").alias("amount"))
summary.write.mode("overwrite").saveAsTable("gold.region_summary")
notebookutils.notebook.exit(summary.count())

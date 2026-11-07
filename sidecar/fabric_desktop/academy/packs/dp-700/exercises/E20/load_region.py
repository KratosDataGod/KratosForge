# Fabric notebook source

# PARAMETERS CELL ********************

region = "all"

# CELL ********************

from pyspark.sql import functions as F

orders = spark.read.table("bronze.orders").where(F.col("region") == region)
orders.write.mode("overwrite").saveAsTable(f"silver.orders_{region}")
notebookutils.notebook.exit(orders.count())

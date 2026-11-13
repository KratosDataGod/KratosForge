# Fabric notebook source

# PARAMETERS CELL ********************

load_date = "2026-01-01"

# CELL ********************

# Lands one day's files: appends that day's orders and customer changes from landing to bronze,
# and exits with how many order rows it landed. Running it twice for a day lands that day twice.
from pyspark.sql import functions as F

orders = spark.read.table("landing.orders").where(F.col("load_date") == load_date)
customers = spark.read.table("landing.customers").where(F.col("load_date") == load_date)
orders.write.mode("append").saveAsTable("bronze.orders")
customers.write.mode("append").saveAsTable("bronze.customers")
notebookutils.notebook.exit(orders.count())

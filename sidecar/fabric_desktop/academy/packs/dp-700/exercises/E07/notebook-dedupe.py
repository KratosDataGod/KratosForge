# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {"name": "synapse_pyspark"}
# META }

# CELL ********************

df = spark.read.table("bronze_orders")
df = df.dropDuplicates(["order_id"])
df.write.mode("overwrite").saveAsTable("silver_orders")

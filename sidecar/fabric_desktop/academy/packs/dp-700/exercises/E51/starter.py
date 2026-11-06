# E51: this notebook appends each day's batch to silver.orders. Today it fails.
# Find out why, fix it here, and run it again. `spark` is ready; check with Check my work.

batch = spark.read.table("bronze.orders_batch")

batch.write.mode("append").saveAsTable("silver.orders")

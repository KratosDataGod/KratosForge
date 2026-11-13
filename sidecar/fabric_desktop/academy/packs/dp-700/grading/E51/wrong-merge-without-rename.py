batch = spark.read.table("bronze.orders_batch")

batch.write.mode("append").option("mergeSchema", "true").saveAsTable("silver.orders")

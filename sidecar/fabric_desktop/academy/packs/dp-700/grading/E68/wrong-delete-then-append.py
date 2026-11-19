from delta.tables import DeltaTable

updates = spark.read.table("bronze.order_updates")

# Avoid the MERGE: delete the orders the batch touches, then append the batch.
keys = ", ".join(str(r["order_id"]) for r in updates.select("order_id").distinct().collect())
DeltaTable.forName(spark, "silver.orders").delete(f"order_id IN ({keys})")
updates.write.mode("append").saveAsTable("silver.orders")

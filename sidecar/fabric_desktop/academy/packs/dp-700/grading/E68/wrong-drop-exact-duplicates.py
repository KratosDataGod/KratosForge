from delta.tables import DeltaTable

updates = spark.read.table("bronze.order_updates")

# Remove the duplicates before merging.
deduped = updates.dropDuplicates()

(DeltaTable.forName(spark, "silver.orders").alias("t")
    .merge(deduped.alias("s"), "t.order_id = s.order_id")
    .whenMatchedUpdateAll()
    .whenNotMatchedInsertAll()
    .execute())

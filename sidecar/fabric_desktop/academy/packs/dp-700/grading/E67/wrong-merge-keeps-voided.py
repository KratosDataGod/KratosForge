from delta.tables import DeltaTable

correction = spark.read.table("bronze.sales_correction").drop("received_date")

# Upsert the corrected rows by sale.
(DeltaTable.forName(spark, "silver.daily_sales").alias("t")
    .merge(correction.alias("s"), "t.sale_id = s.sale_id")
    .whenMatchedUpdateAll()
    .whenNotMatchedInsertAll()
    .execute())

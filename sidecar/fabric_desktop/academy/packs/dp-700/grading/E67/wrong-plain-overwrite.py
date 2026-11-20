correction = spark.read.table("bronze.sales_correction").drop("received_date")

# Replace the day with the corrected rows.
correction.write.mode("overwrite").saveAsTable("silver.daily_sales")

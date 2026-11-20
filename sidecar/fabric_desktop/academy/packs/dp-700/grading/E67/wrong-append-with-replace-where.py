correction = spark.read.table("bronze.sales_correction").drop("received_date")
day = correction.select("sale_date").distinct().collect()[0]["sale_date"]

# Add the corrected rows for that day.
(correction.write.mode("append")
    .option("replaceWhere", f"sale_date = '{day}'")
    .saveAsTable("silver.daily_sales"))

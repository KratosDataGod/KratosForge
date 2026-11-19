correction = spark.read.table("bronze.sales_correction").drop("received_date")
day = correction.select("sale_date").distinct().collect()[0]["sale_date"]

# Reload from the corrected day.
(correction.write.mode("overwrite")
    .option("replaceWhere", f"sale_date >= '{day}'")
    .saveAsTable("silver.daily_sales"))

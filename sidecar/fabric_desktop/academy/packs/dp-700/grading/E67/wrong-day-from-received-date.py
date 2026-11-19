correction = spark.read.table("bronze.sales_correction")
day = correction.select("received_date").distinct().collect()[0]["received_date"]

# Replace the day the file is for.
(correction.drop("received_date").write.mode("overwrite")
    .option("replaceWhere", f"sale_date = '{day}'")
    .saveAsTable("silver.daily_sales"))

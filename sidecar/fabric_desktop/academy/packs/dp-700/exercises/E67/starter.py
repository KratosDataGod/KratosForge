# E67: a corrected file for one business day has arrived in bronze.sales_correction. It holds the
# whole day as it should be. Today's job appends it, so the day is loaded twice and a rerun adds it
# again. Replace that one day in silver.daily_sales and leave every other day as it is.
# `spark` is ready; check with Check my work.

correction = spark.read.table("bronze.sales_correction").drop("received_date")

correction.write.mode("append").saveAsTable("silver.daily_sales")

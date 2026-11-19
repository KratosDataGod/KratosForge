from pyspark.sql import functions as F

correction = spark.read.table("bronze.sales_correction").drop("received_date")

# The business day the file corrects, read from the data rather than typed in.
days = [r["sale_date"] for r in correction.select("sale_date").distinct().collect()]
assert len(days) == 1, f"the correction file should hold one business day, not {len(days)}"
day = days[0]

# Overwrite only that day's rows; every row written belongs to it, as replaceWhere requires.
(correction.write.mode("overwrite")
    .option("replaceWhere", f"sale_date = '{day}'")
    .saveAsTable("silver.daily_sales"))

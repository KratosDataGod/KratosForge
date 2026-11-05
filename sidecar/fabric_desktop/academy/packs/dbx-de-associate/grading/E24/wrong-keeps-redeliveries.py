from pyspark.sql import Window
from pyspark.sql import functions as F

events = spark.read.table("bronze.order_events")
latest = Window.partitionBy("customer_id").orderBy(F.col("valid_from").desc())
customers = (
    spark.read.table("bronze.customer_versions")
    .withColumn("rn", F.row_number().over(latest))
    .filter(F.col("rn") == 1)
    .select("customer_id", "country")
)
by_country = (
    events.join(customers, on="customer_id", how="inner")
    .groupBy("country")
    .agg(
        F.countDistinct("customer_id").alias("customers"),
        F.count("*").alias("orders"),
        F.sum("amount").cast("decimal(12,2)").alias("revenue"),
        F.round(F.avg("amount"), 2).alias("avg_order"),
    )
)
by_country.write.mode("overwrite").saveAsTable("gold.revenue_by_country")

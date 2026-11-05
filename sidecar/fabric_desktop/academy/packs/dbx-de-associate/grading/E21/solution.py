from pyspark.sql import functions as F

web = spark.read.table("bronze.web_orders").withColumn("channel", F.lit("web"))
store = spark.read.table("bronze.store_orders").withColumn("channel", F.lit("store"))
customers = spark.read.table("bronze.customers")

orders = web.unionByName(store)
enriched = (
    orders.join(customers, on=["customer_id", "region"], how="left")
    .withColumn("customer_name", F.coalesce(F.col("name"), F.lit("unknown")))
    .select("order_id", "customer_id", "region", "channel", "amount", "customer_name")
)
enriched.write.mode("overwrite").saveAsTable("silver.orders")

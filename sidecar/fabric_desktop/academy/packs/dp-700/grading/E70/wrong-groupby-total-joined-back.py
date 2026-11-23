from pyspark.sql import functions as F

orders = spark.read.table("silver.orders")

totals = orders.groupBy("customer_id").agg(
    F.count("order_id").alias("order_seq"),
    F.sum("amount").alias("running_amount"),
)
gold = (
    orders.join(totals, on="customer_id", how="inner")
    .select("customer_id", "order_id", "order_date", "amount", "order_seq", "running_amount")
)
gold.write.mode("overwrite").saveAsTable("gold.customer_running_totals")

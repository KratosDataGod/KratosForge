from pyspark.sql import functions as F

events = spark.read.table("bronze.order_events")

schema = ("order_id INT, customer_id STRING, placed_at TIMESTAMP, "
          "items ARRAY<STRUCT<sku: STRING, qty: INT, price: DECIMAL(10,2)>>")
orders = events.withColumn("o", F.expr(f"from_json(payload, '{schema}')"))
lines = orders.select(
    F.col("o.order_id").alias("order_id"),
    F.col("o.customer_id").alias("customer_id"),
    F.col("o.placed_at").alias("placed_at"),
    F.explode(F.col("o.items")).alias("item"),
)
silver = lines.select(
    "order_id", "customer_id", "placed_at",
    F.col("item.sku").alias("sku"),
    F.col("item.qty").alias("qty"),
    F.col("item.price").alias("price"),
)
silver.write.mode("overwrite").saveAsTable("silver.order_lines")

from pyspark.sql import functions as F

raw = spark.read.table("bronze.raw_orders")
products = spark.read.table("bronze.products")

orders = (
    raw.withColumn("status", F.lower(F.trim(F.col("status"))))
    .filter(F.col("status") == "complete")
    .withColumn("order_id", F.col("order_id").cast("int"))
    .withColumn("order_date", F.col("order_date").cast("date"))
    .withColumn("qty", F.col("qty").cast("int"))
    .withColumn("unit_price", F.col("unit_price").cast("decimal(10,2)"))
    .filter(F.col("order_date").isNotNull() & F.col("qty").isNotNull())
    .dropDuplicates(["order_id"])
)
silver = (
    orders.join(products, on="product_id", how="left")
    .withColumn("category", F.coalesce(F.col("category"), F.lit("unknown")))
    .withColumn("amount", (F.col("qty") * F.col("unit_price")).cast("decimal(10,2)"))
    .select("order_id", "order_date", "product_id", "category", "qty", "amount")
)
silver.write.mode("overwrite").saveAsTable("silver.orders")

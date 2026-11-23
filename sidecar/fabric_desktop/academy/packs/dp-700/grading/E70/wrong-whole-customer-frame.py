from pyspark.sql import functions as F
from pyspark.sql.window import Window

orders = spark.read.table("silver.orders")

by_customer = Window.partitionBy("customer_id").orderBy("order_date", "order_id")
running = by_customer.rowsBetween(Window.unboundedPreceding, Window.unboundedFollowing)
gold = (
    orders.withColumn("order_seq", F.row_number().over(by_customer))
    .withColumn("running_amount", F.sum("amount").over(running))
    .select("customer_id", "order_id", "order_date", "amount", "order_seq", "running_amount")
)
gold.write.mode("overwrite").saveAsTable("gold.customer_running_totals")

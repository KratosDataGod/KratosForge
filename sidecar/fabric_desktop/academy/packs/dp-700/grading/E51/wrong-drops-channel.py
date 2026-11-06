from pyspark.sql import functions as F

batch = spark.read.table("bronze.orders_batch")

# The source renamed amount to order_amount: map it back, with silver's type.
fixed = (batch.withColumnRenamed("order_amount", "amount")
              .withColumn("amount", F.col("amount").cast("decimal(10,2)")))

# Keep the table as it was: leave the new column out.
fixed.drop("channel").write.mode("append").saveAsTable("silver.orders")

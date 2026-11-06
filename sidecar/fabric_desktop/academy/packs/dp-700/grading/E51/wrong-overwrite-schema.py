from pyspark.sql import functions as F

batch = spark.read.table("bronze.orders_batch")

# The source renamed amount to order_amount: map it back, with silver's type.
fixed = (batch.withColumnRenamed("order_amount", "amount")
              .withColumn("amount", F.col("amount").cast("decimal(10,2)")))

# channel is new: let the append add it to the table. Earlier rows get null.
fixed.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("silver.orders")

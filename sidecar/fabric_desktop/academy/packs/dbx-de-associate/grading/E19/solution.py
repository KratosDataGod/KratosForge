from pyspark.sql import functions as F

bronze = spark.read.table("bronze.orders")
status = F.lower(F.trim(F.col("status")))
silver = (
    bronze
    .filter(F.col("order_id").isNotNull())
    .withColumn("order_id", F.trim(F.col("order_id")).cast("bigint"))
    .withColumn("customer_id", F.trim(F.col("customer_id")).cast("bigint"))
    .withColumn("order_date", F.coalesce(F.to_date(F.col("order_ts"), "yyyy-MM-dd HH:mm:ss"),
                                         F.to_date(F.col("order_ts"), "MM/dd/yyyy")))
    .withColumn("amount", F.when(F.trim(F.col("amount")) == "", None)
                .otherwise(F.regexp_replace(F.col("amount"), ",", "")).cast("decimal(10,2)"))
    .withColumn("status", F.when(status == "", "unknown").otherwise(status))
    .dropDuplicates(["order_id"])
    .select("order_id", "customer_id", "order_date", "amount", "status")
)
silver.write.mode("overwrite").saveAsTable("silver.orders")

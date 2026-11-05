from pyspark.sql import functions as F

readings = spark.read.table("bronze.readings")
rule = F.col("device_id").isNotNull() & F.col("reading_ts").isNotNull() & (F.col("temperature") >= -50) & (F.col("temperature") <= 60)
reason = (
    F.when(F.col("device_id").isNull(), "missing_device")
    .when(F.col("reading_ts").isNull(), "missing_time")
    .otherwise("temperature_out_of_range")
)

valid = readings.filter(rule)
quarantine = readings.filter(~rule).withColumn("reason", reason)

valid.write.mode("overwrite").saveAsTable("silver.readings")
quarantine.write.mode("overwrite").saveAsTable("silver.readings_quarantine")

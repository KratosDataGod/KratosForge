from pyspark.sql import functions as F

readings = spark.read.table("bronze.readings")
reason = (
    F.when(F.col("device_id").isNull(), "missing_device")
    .when(F.col("reading_ts").isNull(), "missing_time")
    .when(F.col("temperature").isNull(), "missing_temperature")
    .when((F.col("temperature") < -50) | (F.col("temperature") > 60), "temperature_out_of_range")
)
checked = readings.withColumn("reason", reason)

valid = readings
quarantine = checked.filter(F.col("reason").isNotNull())

valid.write.mode("overwrite").saveAsTable("silver.readings")
quarantine.write.mode("overwrite").saveAsTable("silver.readings_quarantine")

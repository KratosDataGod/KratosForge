from pyspark.sql import functions as F

events = spark.read.table("bronze.device_events").dropDuplicates(["event_id"])
readings = (
    events
    .withColumn("reading", F.col("readings")[0])
    .select(
        "event_id",
        F.col("device.id").alias("device_id"),
        F.col("device.site.code").alias("site_code"),
        F.col("reading.metric").alias("metric"),
        F.col("reading.value").alias("value"),
    )
)
readings.write.mode("overwrite").saveAsTable("silver.readings")

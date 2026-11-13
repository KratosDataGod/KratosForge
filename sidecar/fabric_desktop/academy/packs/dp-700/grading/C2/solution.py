from delta.tables import DeltaTable
from pyspark.sql import functions as F


def window_start(column):
    # The start of the 5-minute window the timestamp falls in.
    return F.from_unixtime(F.floor(F.unix_timestamp(column) / 300) * 300).cast("timestamp")


def process(batch, batch_id):
    # 1. One row per event: drop repeats inside the batch, and insert only events Silver hasn't got.
    events = batch.dropDuplicates(["event_id"])
    (DeltaTable.forName(spark, "silver.readings").alias("t")
        .merge(events.alias("s"), "t.event_id = s.event_id")
        .whenNotMatchedInsertAll()
        .execute())

    # 2. Recompute each window this batch touched from everything Silver holds for it, so a late event
    #    joins the readings already there, and MERGE those windows into Gold.
    touched = events.select("device_id", window_start("event_time").alias("window_start")).distinct()
    readings = spark.read.table("silver.readings").withColumn("window_start", window_start("event_time"))
    windows = (readings.join(touched, ["device_id", "window_start"])
        .groupBy("device_id", "window_start")
        .agg(F.count("*").alias("readings"),
             F.round(F.avg("temperature"), 2).cast("decimal(6,2)").alias("avg_temp"),
             F.max("temperature").alias("max_temp")))
    (DeltaTable.forName(spark, "gold.device_5min").alias("t")
        .merge(windows.alias("s"), "t.device_id = s.device_id AND t.window_start = s.window_start")
        .whenMatchedUpdateAll()
        .whenNotMatchedInsertAll()
        .execute())


query = (spark.readStream.table("bronze.device_events")
    .writeStream
    .foreachBatch(process)
    .option("checkpointLocation", "Files/checkpoints/device_events")
    .trigger(availableNow=True)
    .start())
query.awaitTermination()

# 3. The alerts follow the windows: every window over the limit, including ones a late event just pushed over
#    or pulled back under.
(spark.read.table("gold.device_5min")
    .where(F.col("avg_temp") > 8.0)
    .select("device_id", "window_start", "avg_temp")
    .write.mode("overwrite").saveAsTable("gold.alerts"))

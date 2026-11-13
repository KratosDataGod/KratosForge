from pyspark.sql import Window
from pyspark.sql import functions as F
from delta.tables import DeltaTable


def upsert(batch, batch_id):
    # One row per order: its newest event in this batch.
    newest_first = Window.partitionBy("order_id").orderBy(F.col("event_time").desc())
    latest = batch.withColumn("rn", F.row_number().over(newest_first)).where("rn = 1").drop("rn")
    (DeltaTable.forName(spark, "silver.orders").alias("t")
        .merge(latest.alias("s"), "t.order_id = s.order_id")
        .whenMatchedUpdateAll(condition="s.event_time > t.event_time")   # a late event never overwrites newer state
        .execute())


query = (spark.readStream.table("bronze.order_events")
    .writeStream
    .foreachBatch(upsert)
    .option("checkpointLocation", "Files/checkpoints/silver_orders")
    .trigger(availableNow=True)
    .start())
query.awaitTermination()

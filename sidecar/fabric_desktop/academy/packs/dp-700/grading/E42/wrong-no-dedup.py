from pyspark.sql import Window
from pyspark.sql import functions as F
from delta.tables import DeltaTable


def upsert(batch, batch_id):
    # One row per order: its newest event in this batch.
    latest = batch
    (DeltaTable.forName(spark, "silver.orders").alias("t")
        .merge(latest.alias("s"), "t.order_id = s.order_id")
        .whenMatchedUpdateAll(condition="s.event_time > t.event_time")   # a late event never overwrites newer state
        .whenNotMatchedInsertAll()
        .execute())


query = (spark.readStream.table("bronze.order_events")
    .writeStream
    .foreachBatch(upsert)
    .option("checkpointLocation", "Files/checkpoints/silver_orders")
    .trigger(availableNow=True)
    .start())
query.awaitTermination()

# E42: this stream appends every order event to silver.orders, so an order with three events gets three
# rows. silver.orders must keep one row per order: its newest state. `spark` is ready.

(spark.readStream.table("bronze.order_events")
    .writeStream
    .option("checkpointLocation", "Files/checkpoints/silver_orders")
    .trigger(availableNow=True)
    .toTable("silver.orders"))

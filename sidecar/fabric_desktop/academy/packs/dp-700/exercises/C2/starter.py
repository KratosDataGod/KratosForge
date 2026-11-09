# C2 Real-time telemetry. Stream the new readings from bronze.device_events and keep three tables right:
#   silver.readings   event_id, device_id, event_time, temperature       one row per event, ever
#   gold.device_5min  device_id, window_start, readings, avg_temp, max_temp   one row per device and 5-minute window
#   gold.alerts       device_id, window_start, avg_temp                  the windows whose avg_temp is over 8.0
# A window starts on a multiple of 5 minutes. avg_temp is rounded to 2 places as DECIMAL(6,2).
# The lesson says what each table must hold. Use a checkpoint under Files/checkpoints/.

query = (spark.readStream.table("bronze.device_events")
    .writeStream
    .foreachBatch(lambda batch, batch_id: print(batch_id, batch.count()))
    .option("checkpointLocation", "Files/checkpoints/device_events")
    .trigger(availableNow=True)
    .start())
query.awaitTermination()

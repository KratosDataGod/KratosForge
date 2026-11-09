# Capstone: real-time chiller telemetry with late and repeated events

Chillers in a warehouse send a temperature reading every few minutes, and an eventstream appends them to a Bronze table. Readings arrive twice when a device retries, and some arrive minutes late after a network drop. Operations watches 5-minute averages and wants an alert for every window over 8.0 degrees, including the ones a late reading pushes over, and none for a window a late reading brings back down.

## What you'll do

Stream new device readings from Bronze with a checkpoint, keep every event once, put late events into the windows they belong to, keep 5-minute window aggregates and an alert table right, and make a rerun change nothing.

## What the tables must hold

- silver.readings has one row per event_id: everything it held before plus each new event once, however many times it arrived.
- gold.device_5min has one row per device_id and window_start (a multiple of 5 minutes) with readings (the count), avg_temp (rounded to 2 places, DECIMAL(6,2)) and max_temp over all the window's readings in silver.readings, including late ones.
- gold.alerts has device_id, window_start and avg_temp for every window in gold.device_5min whose avg_temp is over 8.0.

## How it runs here

Your code runs as one notebook on this PC with DuckDB's PySpark API and Delta tables written by delta-rs. readStream.table with trigger(availableNow=True) reads the rows Bronze gained since the checkpoint in one batch and hands it to your foreachBatch function, as it would in Fabric.

## The data

bronze.device_events holds the readings sent since the last run; silver.readings, gold.device_5min and gold.alerts are what that run left. The readings are made up and the same every time; the grading also runs your code on other chillers, where a late reading clears an alert.

## Before you start

- E26
- E42
- E43

## What this run proves

- After your code runs, silver.readings, gold.device_5min and gold.alerts hold the right rows on the exercise data and on data you haven't seen, starting from what a previous run left, on this PC.
- Each window appears once, and running your code again changes nothing.

## What it doesn't prove

- It doesn't run on Apache Spark or in Fabric. The stream here reads the table's new rows in one availableNow batch; Spark's withWatermark and window() aren't part of this engine, so windows are computed from the timestamp and late events are handled by recomputing windows.
- The eventstream, the eventhouse and KQL: choosing an engine is E40, an eventstream design is E41, KQL is E34.
- Fabric alerts: an Activator rule on these windows is E46.

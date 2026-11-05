# Broken: a retried pipeline doubled a table

Yesterday's sales load failed after writing its rows, the pipeline's retry policy ran it again, and the run history shows success. Today revenue for batch b2 is double. The load step is the SQL in your workspace, and load_log is the pipeline's run history.

## What you'll do

Use a pipeline's run history to find the rows a failed-then-retried run left behind, remove exactly those, and replace the append with a load that's safe to retry.

## Symptom

Sales 3 and 4 appear twice in silver_sales; revenue for b2 is doubled.

## Before you start

- E37

## What this run proves

- Your repair and your new load ran on this PC with DuckDB: the failed run's rows are gone, the load ran twice for batch b3 without doubling, and load_log is untouched.

## What it doesn't prove

- A Fabric pipeline's retry settings or its monitoring view: the run history here is a recorded table, not the monitoring hub.
- Delta MERGE in a lakehouse, which has the same shape but runs in Spark (see E22).

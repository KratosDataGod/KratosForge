# Structured streaming with foreachBatch and MERGE

bronze.order_events receives every change to every order. silver.orders should hold one row per order with its latest status, and today's stream appends each event as a new row instead. Some events also arrive late, after a newer one was already loaded.

## What you'll do

Stream order events into a current-state table with foreachBatch: keep each order's newest event per batch, and MERGE it so new orders are added, newer events update, and late events never overwrite newer state.

## What the tables must hold

- silver.orders has one row per order_id.
- Each order's row is its newest event, whether it came from today's events or was already in silver.orders.
- Orders that had no events today stay as they were.

## How it runs here

Your answer is the notebook's PySpark code. It runs on this PC with DuckDB's PySpark API, not Apache Spark, on Delta tables and files in the exercise's own folder, with no sign-in and no network. Every stream here runs as trigger(availableNow=True): it takes what has arrived, writes it and stops, and its checkpoint under Files/ remembers where it got to.

## Before you start

- E26

## What this run proves

- After your stream runs, silver.orders holds each order's newest state once, on the exercise data and on data you haven't seen, and a second run leaves it the same, on this PC.

## What it doesn't prove

- It doesn't run on Apache Spark or in Fabric: the stream runs as availableNow and the MERGE runs on delta-rs. Spark refuses a MERGE where several source rows match one target row; delta-rs doesn't, so here that mistake shows up as duplicate rows instead of an error.
- Ties: two events for one order at the same time. The data has none.
- Deletes, and watermarks for late data in stateful streaming aggregations.

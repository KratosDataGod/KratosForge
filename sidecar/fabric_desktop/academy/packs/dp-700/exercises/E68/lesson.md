# Broken: a notebook MERGE fails because the source has two rows for one key

A notebook merges each day's order updates into silver.orders, which holds one row per order. Today it fails at the MERGE. The source now sends every change to an order, not just the last one, and sometimes sends the same change twice. silver.orders must end up with each order's newest state, and the orders that had no changes today must stay as they are.

## What you'll do

Read the error from a failing notebook MERGE, find the repeated keys in the source batch behind it, and keep each order's newest change so the MERGE runs, updates and inserts the right rows, and is safe to run again.

## Symptom

The MERGE stops with DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE: multiple source rows matched the same target row. Nothing was changed in silver.orders.

## Why Delta refuses it

A MERGE matches each target row with the source rows its condition finds. When one target row matches two source rows, either could be the one that changes it, and the result would depend on the order they were applied in. Delta doesn't guess: it stops the whole MERGE. The condition isn't wrong; the source has more than one row for a key.

## What the table must hold

- The MERGE runs: the source holds one row per order_id.
- Each order that changed today gets its newest change, by updated_at.
- New orders are added once, with their newest values.
- Orders with no change today stay as they were, and a second run leaves the table the same.

## How it runs here

Your answer is the notebook's PySpark code. It runs on this PC with DuckDB's PySpark API, not Apache Spark, on Delta tables in the exercise's own folder, with no sign-in and no network. `from delta.tables import DeltaTable` works as in a notebook; the MERGE runs on delta-rs, and the same one-match rule is checked before it, so the same mistake fails here with the same error name.

## Before you start

- LM-11

## What this run proves

- After your notebook runs, silver.orders holds each order's newest state once, with untouched orders unchanged, on the exercise data and on data you haven't seen, on this PC.
- order_id is unique and never empty, the types are kept, and a second run leaves silver.orders the same.

## What it doesn't prove

- It doesn't run on Apache Spark or in a Fabric notebook. The MERGE here runs on delta-rs, and the one-match check is made by the Academy's own code before it; it uses Delta's error name, but the full message is worded differently from Spark's.
- Ties: two changes for one order with the same updated_at. The data has none; a real load needs a second column to break them.
- Late changes older than what silver.orders already holds: the MERGE here updates every matched order. E42 adds that condition.
- How Fabric shows the failure (the notebook's cell output and the Spark monitoring pages).

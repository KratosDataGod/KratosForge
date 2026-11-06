# Broken: a notebook append fails after the source schema changed

A notebook appends each day's orders to silver.orders. Today it fails at the write. Overnight the source system renamed amount to order_amount and started sending a channel column, which sales wants to keep. Yesterday's rows have to stay as they are.

## What you'll do

Read the error from a failing notebook append, find the schema change behind it, and fix the load so today's batch lands with yesterday's rows untouched and the new column kept.

## Symptom

The append stops with a schema mismatch error: the batch has five columns and the table has four.

## What the table must hold

- Yesterday's rows are still in silver.orders, unchanged.
- Today's rows are added with their amount in the amount column, as decimal(10,2).
- silver.orders gains a channel column; yesterday's rows have no channel (null).

## How it runs here

Your answer is the notebook's PySpark code. It runs on this PC with DuckDB's PySpark API, not Apache Spark, on Delta tables in the exercise's own folder, with no sign-in and no network. The Delta tables keep their schema the same way a lakehouse table does, so the same append is refused here and in Fabric, though the error message is worded differently.

## Before you start

- LM-08

## What this run proves

- After your notebook runs, silver.orders holds yesterday's rows unchanged and today's rows with the right amount and channel, on the exercise data and on data you haven't seen, on this PC.
- amount is still a decimal, and order_id and amount are never empty.

## What it doesn't prove

- It doesn't run on Apache Spark or in a Fabric notebook. The error you see here comes from delta-rs and is worded differently from Spark's, though both refuse the same append.
- That a rerun is safe: appending the same batch twice would add it twice. Making the daily load rerunnable is another exercise (E22, MERGE).
- How Fabric shows the failure (the notebook's cell output and the Spark monitoring pages).

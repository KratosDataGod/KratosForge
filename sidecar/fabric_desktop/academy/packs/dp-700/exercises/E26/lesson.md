# Streaming load pattern: availableNow with a checkpoint into Bronze

Order files land in Files/landing/orders once a day, and a stream loads them into bronze.orders. Someone named the checkpoint folder after the time the load starts, so every run, including a rerun after a failure, loads every old file again. Finance also noticed amount arrives as text.

## What you'll do

Fix a Structured Streaming load into Bronze so that its checkpoint and trigger(availableNow=True) make each run take only new files, with the right column types.

## What the tables must hold

- bronze.orders holds each order from the landing files once, however many times the load runs.
- order_id is an integer, customer is text and amount is a double.

## How it runs here

Your answer is the notebook's PySpark code. It runs on this PC with DuckDB's PySpark API, not Apache Spark, on Delta tables and files in the exercise's own folder, with no sign-in and no network. Every stream here runs as trigger(availableNow=True): it takes what has arrived, writes it and stops, and its checkpoint under Files/ remembers where it got to.

## What this run proves

- After your load runs, and again after it runs a second time, bronze.orders holds each landed order once, with the right types, on this PC.

## What it doesn't prove

- It doesn't run on Apache Spark or in Fabric: every stream here runs as availableNow, taking what has arrived and stopping. A stream left running with a processingTime trigger isn't shown.
- New files arriving between runs: the files are fixed, so the second run finds nothing new.
- Running the stream in production, which Fabric's guidance puts in a Spark job definition with a retry policy.

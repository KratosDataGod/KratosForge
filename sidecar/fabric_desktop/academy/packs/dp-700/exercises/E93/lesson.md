# Broken: a Bronze to Silver stream that shares a checkpoint and stops after a cleanup

Order files land in Files/landing/orders. One notebook streams them into bronze.orders, streams bronze.orders into silver.orders for reports, and then deletes test orders from Bronze. Someone pointed both streams at the same checkpoint folder, and the Silver step fails on the very first run. The team also expects trouble the next morning, because the cleanup rewrites Bronze after the Silver stream has read it.

## What you'll do

Fix a two-hop streaming load, landing files to Bronze and Bronze to Silver, so that each stream keeps its own checkpoint, the Silver stream survives deletes in Bronze, and Silver never holds what the cleanup removes.

## Symptom

The first run stops at the Silver step with an error that says the checkpoint belongs to another source. Fix that, and the first run works, but silver.orders holds two test orders. Run it again, and the Silver stream stops because bronze.orders had rows deleted.

## What to know

A streaming load in layers is a chain of queries. Here the first query takes new files into Bronze. The second takes new Bronze rows into Silver. Each query keeps its progress in its checkpoint.

- **One checkpoint per query.** The checkpoint records which files or table versions a query has taken, so the next run carries on from there. Each query must have a different checkpoint location, and queries should never share one. If you delete a checkpoint or point the query at a new folder, the next run begins fresh and takes everything again.
- **A Delta source takes appends.** A stream that reads a Delta table only accepts appended rows. When an UPDATE, DELETE, MERGE INTO or OVERWRITE changes the table, the stream fails with an error.
- **skipChangeCommits.** Set `.option("skipChangeCommits", "true")` on the readStream to pass over commits that delete or modify rows and process only appends. The change isn't carried downstream. If Silver must not keep a row that Bronze removed, Silver needs its own logic for that.
- **ignoreDeletes is narrower.** It only covers deletes of whole partitions. A DELETE with a condition, like this cleanup, isn't one.

Sources on Microsoft Learn:

- [Structured Streaming checkpoints](https://learn.microsoft.com/en-us/azure/databricks/structured-streaming/checkpoints)
- [Delta table streaming reads and writes](https://learn.microsoft.com/en-us/azure/databricks/structured-streaming/delta-lake)
- [Get streaming data into a lakehouse with Spark structured streaming](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-streaming-data)

## What the tables must hold

- Each streaming query has its own checkpoint folder, kept the same from run to run.
- bronze.orders holds the four real orders once, after the cleanup.
- silver.orders holds the four real orders once and never a test order.
- A second run works and leaves both tables as they were. Keep the cleanup step, and keep it last.

## How it runs here

Your answer is the notebook's PySpark code. It runs on this PC with DuckDB's PySpark API, not Apache Spark, on Delta tables and files in the exercise's own folder, with no sign-in and no network. Every stream here runs as trigger(availableNow=True). It takes what has arrived, writes it and stops, and its checkpoint under Files/ remembers where it got to. The check runs your notebook, then runs it again, the way the next morning's run would.

## What this run proves

- After your notebook runs, bronze.orders and silver.orders hold the four real orders once each, with the right types, on this PC.
- A second run of your notebook succeeds after the cleanup's deletes and leaves both tables as they were.

## What it doesn't prove

- It doesn't run on Apache Spark or in Fabric. Every stream here runs as availableNow, taking what has arrived and stopping. The error for a shared checkpoint is this lab's own wording, not Spark's.
- New files arriving between runs. The files are fixed, so the second run finds nothing new in the landing folder.
- Where the cleanup runs. Moving it before the Silver stream also passes here, because the files are fixed, but a test order that lands tomorrow would bring the same failure back. Keep it last, as the scenario says.
- That Bronze keeps raw rows. Filtering test orders out in the Bronze stream instead of deleting them also passes here.

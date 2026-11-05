# Bronze to Silver that survives a rerun

Bronze keeps what the source sent, as text, duplicates and all (LM-06). Silver is the cleaned,
typed, one-row-per-key version that everything downstream trusts. The step between them has to be
right, and it has to be safe to run again (LM-21).

## What the step does

1. **Type** each column, and drop or quarantine rows that won't convert (`TRY_CAST` gives NULL
   instead of failing).
2. **De-duplicate** to one row per key, keeping the newest by when it was ingested (LM-17).
3. **Write** so a rerun gives the same table: rebuild it (`CREATE OR REPLACE TABLE ... AS`), or
   replace by key for big tables.

In a Fabric or Databricks notebook the same three steps are a few PySpark or Polars calls, then a
Delta write with `mode("overwrite")` or a `MERGE`.

## The lab

`bronze_orders` has a corrected order (2), an exact duplicate (4) and a row whose date isn't a
date (3). Build `silver_orders`.

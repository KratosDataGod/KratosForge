# Reload one day of a Delta table with replaceWhere

silver.daily_sales is loaded one business day at a time. The source has sent a corrected file for one day: it holds that whole day as it should be, with an amount fixed, a voided sale gone and a missed sale added. Today's job appends it, so the day is loaded twice. Reload just that day.

## What you'll do

Replace one business day of a Delta table with a corrected file using an overwrite with replaceWhere, so that day's rows are exactly the file's, every other day is untouched, and a rerun changes nothing.

## Replace a slice, not the table

A Delta overwrite normally replaces every row of the table. With the replaceWhere option it replaces only the rows that match a condition:

```python
(df.write.mode("overwrite")
    .option("replaceWhere", "region = 'EU'")
    .saveAsTable("sales"))
```

The rows that match are removed and `df`'s rows are written, in one commit. Delta also checks the other direction: every row in `df` must match the condition, or the write fails with DELTA_REPLACE_WHERE_MISMATCH and nothing changes. The option only applies to an overwrite; an append ignores it.

## What the table must hold

- The corrected day in silver.daily_sales holds exactly the file's rows: the fixed amount, no voided sale, and the missed sale.
- Every other day is exactly as it was.
- The table keeps its four columns; received_date isn't one of them.
- Running the job again leaves the table the same.

## How it runs here

Your answer is the job's PySpark code. It runs on this PC with DuckDB's PySpark API, not Apache Spark, on Delta tables in the exercise's own folder, with no sign-in and no network. replaceWhere is run as Delta runs it: only the matching rows are replaced, and rows that don't match the condition stop the write.

## Before you start

- LM-21

## What this run proves

- After your job runs, silver.daily_sales holds the corrected day exactly as the file has it and every other day unchanged, on the exercise data and on data you haven't seen, on this PC.
- sale_id is unique and never empty, the columns and types are kept, and a second run leaves the table the same.

## What it doesn't prove

- It doesn't run on Apache Spark, in a Fabric notebook or on a Fabric lakehouse: the overwrite runs on delta-rs, and the replaceWhere check is the Academy's own, made before the write. It uses Delta's error name, but the message is worded differently from Spark's.
- How the table is laid out: partitionBy does nothing here, so whether the day is a partition, how many files the overwrite rewrites, and V-Order aren't checked.
- Correction files that span several days, or that arrive while another job writes the same table.

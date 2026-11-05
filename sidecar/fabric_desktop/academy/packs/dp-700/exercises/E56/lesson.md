# Broken: a slow lakehouse table made of many small files

Device events arrive in 40 small batches a day, and the ingestion job appends each one as it lands. Readers complain the table gets slower every day. The table has the right rows; it's how they're stored that hurts.

## What you'll do

Find why a Delta table is slow to read, compact its small files without losing or doubling rows, and keep its history.

## Symptom

tables/events has 40 data files for 1,000 rows.

## How it runs here

Your answer is a Python script. It runs on this PC with delta-rs (the `deltalake` package) and Polars, in the exercise's own folder, with no sign-in and no network. A Fabric lakehouse stores tables in the same Delta format, but there you'd usually write this with Spark in a notebook.

## Before you start

- LM-25

## What this run proves

- Your script ran on this PC and left a Delta table with at most 2 data files, an OPTIMIZE in its history after the writes, and all 1,000 events once each.

## What it doesn't prove

- Read speed in Fabric: this checks file counts in the Delta log, not query timings. V-Order, OPTIMIZE ZORDER BY and table maintenance in a Fabric lakehouse are separate features.
- Statistics and their effect on the SQL analytics endpoint.

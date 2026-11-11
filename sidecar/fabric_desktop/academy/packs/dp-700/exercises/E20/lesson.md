# Notebook orchestration with runMultiple, parameters and exit values

Two notebooks already exist: load_region loads one region's orders and exits with how many rows it loaded, and build_summary totals the regions it's given. New regions appear without warning, so the orchestration can't list them by hand. Operations also wants a log of the rows each load reported.

## What you'll do

Run a parameterized notebook once per region, then a summary notebook after all of them, from one orchestrating notebook, and use the exit values the notebooks return.

## What the tables must hold

- gold.region_summary has one row per region in bronze.orders, with its order count and total amount.
- ops.load_log has one row per region with rows_loaded, the integer that region's load_region run exited with.

## How it runs here

Your answer is the notebook's PySpark code. It runs on this PC with DuckDB's PySpark API, not Apache Spark, on Delta tables and files in the exercise's own folder, with no sign-in and no network. The notebooks load_region and build_summary are in notebooks/, in Fabric's .py notebook format, and notebookutils runs them the way a Fabric notebook does: arguments replace the parameters cell's values, and run or runMultiple returns what each notebook passed to exit.

## What this run proves

- After your notebook runs, gold.region_summary and ops.load_log hold the right rows for every region, on the exercise data and on data with regions you haven't seen, on this PC.
- rows_loaded is an integer, and running your notebook again leaves the same tables.

## What it doesn't prove

- It doesn't run on Apache Spark or in Fabric. Here runMultiple runs the notebooks one at a time, in an order that respects their dependencies; in Fabric they run concurrently on one Spark session, up to its concurrency limit.
- Fabric's timeouts, retries and cross-workspace notebook references.
- Running the notebooks from a pipeline's Notebook activity instead.

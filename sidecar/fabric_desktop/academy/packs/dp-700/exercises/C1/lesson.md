# Capstone: retail lakehouse from landing to a Gold star

A shop drops each day's orders and customer changes into landing. Orders get sent twice, corrected days later, or sent for customers who don't exist yet; customer changes arrive out of order. Finance wants daily sales by customer segment as it was when each sale happened, and operations wants proof that no day lands twice.

## What you'll do

Build a small lakehouse end to end: land new days through a parameterised notebook, MERGE them into Silver with quality checks, keep a type 2 customer dimension, and build a Gold fact table and a denormalised aggregate, so that a rerun changes nothing.

## What the tables must hold

- ops.load_log has one row per day ever landed, with rows_landed, the number land_day exited with for that day.
- silver.orders has one row per order: its newest load. An order whose newest row has no customer_id or a quantity that isn't positive isn't in it.
- silver.orders_rejected has those orders: order_id, the load_date of their newest row, and reason 'missing customer_id' or 'quantity not positive' (the first that applies).
- silver.dim_customer has a version per change of city or segment, in changed_at order; a row that changes neither adds no version. customer_key is customer_id * 100 + the version number from 1. valid_to is the next version's valid_from, NULL for the current one. name is the latest name on every version.
- gold.fact_sales has a row per order in silver.orders with the customer_key of the version valid at order_ts, or -1 when there isn't one; order_date is the order_ts date and amount is qty * unit_price.
- gold.sales_by_segment has orders and amount per order_date, segment (from the version on the sale, 'Unknown' for -1) and product category.

## How it runs here

Your code runs as one notebook on this PC with DuckDB's PySpark API and Delta tables written by delta-rs. notebookutils.notebook.run runs land_day from notebooks/ with its parameters cell replaced, as Fabric does.

## The data

landing.orders and landing.customers hold three days of files, each row tagged with the load_date it arrived on. bronze.products is the product list. The data is made up and the same every time; the grading also runs your code on four days of other data, where day 1 has already landed and been merged.

## Before you start

- E20
- E22
- E24
- E25
- E37

## What this run proves

- After your code runs, the six tables hold the right rows on the exercise data and on data you haven't seen, starting from a lakehouse where a day has already landed, on this PC.
- No day lands twice, the dimension has a unique surrogate key, and running your code again changes nothing.

## What it doesn't prove

- It doesn't run on Apache Spark or in Fabric, and land_day runs here as a notebook called from yours, not from a pipeline's Notebook activity. Pipelines are E31 and E19.
- Table maintenance: OPTIMIZE, V-Order and VACUUM on the Gold tables are E56, and query tuning is E59 and E64.
- How you build each table: the grading checks the tables, so a full rebuild of Silver that gives the same rows also passes.

# Load a fact when its customer arrives late (inferred members)

Point-of-sale sales reach the lakehouse minutes after they happen, but new customers come from the CRM export once a night. Yesterday's load dropped three sales because their customers weren't in dim_customer yet, and finance noticed the total was short.

## What you'll do

Load every fact row even when its dimension member hasn't arrived, then complete that member in place when it does, without changing the fact's key.

## Before you start

- E23

## What this run proves

- Your statements ran on this PC with DuckDB: every sale is in fact_sales, every fact key exists in dim_customer, and the late customer kept its key.
- Each customer_id appears once in dim_customer.

## What it doesn't prove

- That the statements run unchanged in a Fabric Warehouse or Spark SQL.
- Concurrency between the fact load and the nightly dimension load.

# Spark SQL: clean, cast and join raw orders in SQL

The orders feed lands in Bronze as text exactly as the shop's system sent it: padded statuses, an amount of 'n/a', and one order sent twice. The analytics team wants a typed silver_orders table with each order's customer region.

## What you'll do

Turn an all-text Bronze table into a typed Silver table with SQL: trim and normalise text, cast safely, drop exact duplicates and rows that can't be repaired, and add a looked-up attribute.

## Before you start

- LM-08

## What this run proves

- Your SQL ran on this PC with DuckDB and silver_orders has the expected rows and column types.
- The functions the reference uses (TRY_CAST, CAST to TIMESTAMP, TRIM, LOWER) also exist in Spark SQL.

## What it doesn't prove

- That your SQL runs in a Fabric notebook's Spark SQL: this run used DuckDB, not Spark. Function names and casting rules differ in places, for example how a malformed timestamp string is handled.
- Writing silver_orders as a Delta table in a lakehouse.

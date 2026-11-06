# Denormalize a star into a wide reporting table

A data science team wants the sales star as one flat table they can load into a notebook without joins. The fact has a sale whose product key was never matched, and the team must still see it, labelled Unknown, or their totals won't match finance's.

## What you'll do

Flatten a fact table and its dimensions into one wide table with one row per fact, keeping every fact even when a dimension row is missing.

## Before you start

- LAB-03

## What this run proves

- Your SQL ran on this PC with DuckDB and gold_sales_wide has one row per sale with the expected attributes and totals.

## What it doesn't prove

- How a wide table performs in Direct Lake or a Fabric Warehouse compared with the star: that depends on the service.
- That the SQL runs unchanged in Spark SQL or T-SQL.

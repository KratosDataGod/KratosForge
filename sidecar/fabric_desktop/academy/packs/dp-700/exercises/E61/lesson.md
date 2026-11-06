# Optimize a warehouse: loads, data types, statistics and result set caching

Brightwater's finance warehouse feeds a dashboard that's getting slow. The nightly load inserts rows one at a time, some columns are much wider than their data, and the same dashboard query runs all day.

## What you'll do

Choose the load pattern, column types and features that keep a Fabric Warehouse fast, and say why.

## Notes

- Many small INSERT, UPDATE or DELETE statements each write a new Parquet file. Stage data and load it together, or use COPY INTO for files.
- Use the smallest string length that fits the values.
- The engine creates and maintains statistics automatically; you can add single-column statistics yourself.
- Result set caching keeps a SELECT's final result for reuse, and drops it when the tables it reads change.

## What this run proves

- You can pick the right setting or pattern for each scenario, with the reason and source shown afterwards.

## What it doesn't prove

- That you can apply it in Fabric: nothing is set up or run here. Doing it on a real item is FABRIC VERIFIED work.

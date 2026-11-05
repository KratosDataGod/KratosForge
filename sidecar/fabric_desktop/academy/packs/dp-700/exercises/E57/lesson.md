# Broken: an incremental load that misses late rows

Finance says order 1006 is in the source system but not in the lakehouse, and the load job reports success every night. The job is the SQL in your workspace: it was copied from a tutorial and has run unchanged for months.

## What you'll do

Diagnose why an incremental load never picks up rows that arrive late, repair the filter and the watermark, and prove the repaired job is safe to run twice.

## Symptom

Order 1006 (ordered 1 April, arrived 3 April) is in source_orders but never reaches silver_orders.

## Before you start

- LM-12
- E21

## What this run proves

- Your repaired job ran twice on this PC with DuckDB; silver_orders got every missing order exactly once and load_state holds the right watermark.
- Nothing outside silver_orders and load_state changed.

## What it doesn't prove

- The same job as a Fabric pipeline or notebook, where the watermark is usually stored in a table or a pipeline variable.
- Source systems whose arrival timestamps can go backwards, which need an overlap window.

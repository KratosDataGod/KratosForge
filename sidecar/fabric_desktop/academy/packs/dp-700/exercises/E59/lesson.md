# Broken: a query that got slow because its filter can't skip data

The month-end revenue query used to return instantly. Someone 'tidied' its filter to year() and month(), and now it reads all two million rows every time. The table is sorted by order_date, as a table loaded day by day usually is.

## What you'll do

Find why a monthly revenue query can't skip data, rewrite the filter so the engine can, and prove the answer didn't change.

## Symptom

The query is many times slower than an equivalent range filter, with the same answer.

## Speed

Time both queries a few times on this PC: the fix is usually two to three times faster here. Timings vary from PC to PC, so this exercise grades the filter (same answer, no function around the column), not the stopwatch.

## Before you start

- LM-18
- LAB-02

## What this run proves

- Your query gave the same answer as the original on this PC with DuckDB.
- Your filter compares order_date as it is, so the engine can skip blocks of rows (no SQ002 finding).

## What it doesn't prove

- A speed-up threshold: on this data DuckDB measured the fix at about 2.1 to 2.7 times faster, too close to a 2x goal to grade reliably, so speed is shown as a measurement, not a pass mark.
- The speed-up in Fabric: Warehouse, the SQL analytics endpoint and Spark skip data by their own rules (file and row-group statistics, V-Order), and timings there differ.
- Anything about capacity units: a local timing is not a CU estimate.

# Late-arriving facts: a reprocessing window that keeps totals right

Tills sometimes upload events a day or two late, and the uploader retries when it times out. daily_sales feeds the morning dashboard. The events table only keeps recent detail; older days exist only as totals in daily_sales, so a full rebuild would wipe them.

## What you'll do

Fold a batch of late and retried events into a daily aggregate by recomputing only the days the batch touches, without double counting and without losing days whose detail has been archived.

## Before you start

- LM-12
- E37

## What this run proves

- Your SQL ran on this PC with DuckDB; daily_sales has the right total for every day, old days are untouched, and the retried event counts once.

## What it doesn't prove

- That the SQL runs unchanged in Spark SQL or a Fabric Warehouse.
- Streaming watermarks in Spark structured streaming, which solve the same problem for streams (see E26 and E42).

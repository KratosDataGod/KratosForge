# Monitor ingestion: the Copy runs that succeeded but didn't load what they should

The Nightly ingest pipeline copies seven source tables into bronze. The monitoring hub showed it green this morning after someone retried a failed run, yet the Sales team says products are missing and the stores table hasn't changed since Saturday. You have the Copy activities the pipeline should run and the runs they recorded. Write the check that would have caught every problem before Sales did.

## What you'll do

From a nightly pipeline's Copy activity runs, write the morning ingestion check: each activity's latest run, and whether it didn't run, failed, copied nothing or skipped rows, so a green status can't hide a bad load.

## How it runs here

In Fabric you'd open the pipeline run from the monitoring hub and read each Copy activity's details and output. A Copy activity's output reports, among other things, rowsRead, rowsCopied, copyDuration in seconds and, when fault tolerance is set to skip incompatible rows, rowsSkipped. The monitoring hub can also retry a completed pipeline run, which adds a new run rather than changing the failed one.

This lab gives you the same facts as three made-up tables: copy_activities (what should run each night), copy_runs (what ran) and ingestion_check (when the check runs).

## What this run proves

- Your query starts from the activities that should have run, takes each one's latest run by its start time, and flags a missing run, a failure, an empty copy and skipped rows in that order, on a simulated run history with DuckDB on this PC.

## What it doesn't prove

- The monitoring hub and the pipeline run details themselves, or where Fabric keeps Copy activity output and how to query it there. The runs here are made up and shaped like a Copy activity's output (rowsRead, rowsCopied, rowsSkipped, copyDuration).
- Why a source returned no rows or which rows were skipped: in Fabric, the Copy activity writes the skipped rows to a log only when logging is turned on in its settings.
- Alerts: sending this result to someone is objective 3.1.4.

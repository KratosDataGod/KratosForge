# Missing data: defaults, quarantine and the Unknown member

The orders feed has gaps: some orders have no channel, some no quantity, some no customer, and one has no amount. The business has agreed rules for each, and audit wants every row that couldn't be loaded kept with its reason.

## What you'll do

Decide, column by column, whether a missing value gets a default, points at the Unknown member, or sends the row to quarantine, and load both tables so no row is lost.

## Before you start

- LM-13
- E23

## What this run proves

- Your SQL ran on this PC with DuckDB; every staged order is either in silver_orders or in quarantine_orders with its reason, and never both.

## What it doesn't prove

- That the SQL runs unchanged in Spark SQL or a Fabric Warehouse.
- Dataflow Gen2 or pipeline features that do the same job, such as a Copy activity's fault tolerance settings.

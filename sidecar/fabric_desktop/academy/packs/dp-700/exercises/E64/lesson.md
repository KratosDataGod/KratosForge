# Read a query plan and fix the expensive step

The monthly revenue report joins orders to their lines and, since last week, to their payments. Finance says both totals jumped when payments were added, and the query got slower. An order has several lines and can have several payments, so joining both to the order pairs every line with every payment.

## What you'll do

Use EXPLAIN ANALYZE to find the join that multiplies rows, then aggregate each side before joining so the query reads fewer rows and the totals are right.

## Reading the plan

Run `EXPLAIN ANALYZE` followed by the query. DuckDB prints each step with the rows it returned. Look for a join whose output is much bigger than its inputs: that's the step that multiplies rows. Run it again on your fix and compare.

## Before you start

- LM-18
- LAB-02

## What this run proves

- Your query returns the right orders, line total and paid total for each region and month, on this PC with DuckDB.

## What it doesn't prove

- A speed-up threshold: the fix reads far fewer rows, but timings vary from PC to PC, so speed is shown as a measurement, not a pass mark.
- How a Fabric Warehouse or the SQL analytics endpoint plans the same query: their plans look different, though the join that multiplies rows does too.
- Anything about capacity units: a local timing is not a CU estimate.

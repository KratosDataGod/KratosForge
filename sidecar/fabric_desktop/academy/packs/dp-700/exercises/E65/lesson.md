# Broken: a nightly T-SQL load stops on a bad value, a zero amount and an order sent twice

The nightly MERGE into fact_orders has failed three nights running and the warehouse is a day behind. Tonight's staged batch has an amount of 'N/A', a quantity of 'two', an order with a zero amount, and two orders the source sent twice with a later version. An order with no amount has no margin: margin_pct stays empty (NULL), not 0. The load step is the SQL in your workspace.

## What you'll do

Read why a T-SQL load stopped and fix it so one bad row can't stop it again: cast safely and quarantine what won't cast, guard a division against zero, and give MERGE one source row per key.

## Symptom

The load stops with a conversion error and fact_orders still holds yesterday's rows.

## Before you start

- E55
- E37

## What this run proves

- Your repaired load ran on this PC with DuckDB: every order that can be loaded is in fact_orders once, at its latest version, with the right margin, and the two that can't are in order_rejects.
- The staged batch is untouched, so the next run and the source team still have it.

## What it doesn't prove

- The exact error messages a Fabric Warehouse gives. DuckDB stops on the same bad cast and on casting a divide-by-zero result, but its messages differ, and it doesn't refuse a MERGE whose source has two rows for one target row the way T-SQL does: here the duplicate shows up as a doubled order instead.
- That your SQL runs unchanged in T-SQL: TRY_CAST, NULLIF, ROW_NUMBER and MERGE exist in both, but this run used DuckDB.

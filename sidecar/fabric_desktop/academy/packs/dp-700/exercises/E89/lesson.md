# Incremental copy in a pipeline: parameters, two watermarks and a safe save

The orders pipeline copies changed rows from the warehouse into the lakehouse every night. It only works for one table, it copies the boundary rows twice, and last week it moved the watermark forward on a night the copy failed. That night's rows were never loaded. The team wants one pipeline that any table can use, through its tableName and watermarkColumn parameters.

## What you'll do

Build a pipeline that copies only the rows changed since the last run: read the old and new watermarks with Lookup activities, build the Copy source query from parameters, and save the new watermark only after the copy succeeds.

## The pattern

A watermark is the change time where the last good run stopped. Each run does four things.

1. **Old watermark** (Lookup) reads the saved mark for this table.
2. **New watermark** (Lookup) reads the highest change time in the source now.
3. **Copy changed rows** (Copy) copies the rows after the old mark and up to the new one.
4. **Save watermark** (Stored procedure) writes the new mark, so the next run starts there.

In this pipeline, **Build query** (Set variable) writes the Copy source query into the `query` variable first. The run then shows the query it used.

## What the pipeline must do

- Build the query from the run's parameters, in exactly this form:

  ```sql
  SELECT * FROM <tableName> WHERE <watermarkColumn> > '<old watermark>' AND <watermarkColumn> <= '<new watermark>'
  ```

- Save the watermark only when the copy succeeds. In the pipeline JSON, the On success condition is written `Succeeded`, On failure is `Failed`, On completion is `Completed` and On skip is `Skipped`.
- Return three values from **Summary**: `query`, `rowsCopied` from the Copy activity's output, and `watermark`, the value the pipeline saved.

## How it runs here

Your answer is the pipeline's JSON (`pipeline.json`), the same shape Fabric shows in the pipeline's JSON view. It runs on the app's local pipeline runner with control-flow activities only. The Lookup, Copy and Stored procedure activities are stand-ins that return fixed results. The old watermark is 2026-11-20T06:00:00 and the new one is 2026-11-21T06:00:00. Check my work runs your pipeline three times. One run is for sales.orders, one is for sales.returns, and in one the copy fails.

## What this run proves

- Your pipeline JSON ran on the local runner on this PC three times. Two runs used different tables and columns, and their queries, row counts and saved watermarks were checked. In the third run the copy failed, and the watermark was not saved.

## What it doesn't prove

- Copying data or reading the watermark table. The Lookup, Copy and Stored procedure activities are stand-ins with fixed results, so no data moves and no connection is tested.
- The parameters of the Stored procedure activity. The runner doesn't evaluate a stand-in's settings, so the saved watermark is checked through the pipeline's return value.
- That the Copy activity starts after Build query. The Copy stand-in returns the same result whenever it starts.
- Running in a Fabric workspace, its schedule or the speed of the copy.

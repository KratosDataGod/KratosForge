# Broken: a pipeline fails on a bad expression and never reports why

The orders load fails every night, and the failure alert that should say why never fires. The run shows Build query failed and everything after it skipped. Tonight the source system is also offline, so once the expression is fixed, the copy will fail too, and that failure has to be logged and reported.

## What you'll do

Read a pipeline run's activity errors, fix a dynamic expression, and repair a dependency condition so a failed copy is logged and reported.

## Symptom

Build query fails with an expression error, and when it's fixed, a failed copy still skips Log failure and Fail run.

## How it runs here

Your answer is the pipeline's JSON (`pipeline.json`), the same shape Fabric shows in the pipeline's JSON view. It runs on the app's local pipeline runner with control-flow activities only. Copy and Lookup activities are stand-ins that return fixed results, so you can practise expressions, dependencies and loops without moving data or using capacity.

## What this run proves

- Your repaired pipeline JSON ran on the local runner on this PC: the query was built from the lookup, the failed copy was logged, and the run reported the failure with its reason.

## What it doesn't prove

- Copying data: the Copy activity here is a stand-in with a fixed result, so no data moves and no connection is tested.
- Fabric's monitoring hub or alerts: the run record here comes from the local runner.

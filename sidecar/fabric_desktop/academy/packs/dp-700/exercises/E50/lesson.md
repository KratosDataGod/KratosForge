# Broken: a Dataflow Gen2 stops loading after two small edits

The Orders dataflow appends to silver_orders every night. Yesterday someone edited two steps: they set amount to Text 'to be safe', and added a step to drop the ingest_file column. Since then nothing new reaches silver_orders. The destination uses a fixed schema, because reports depend on its types.

## What you'll do

Read a Dataflow Gen2 query's applied steps against its data destination, find the step that names a column that isn't there and the type that no longer matches, and fix the query without changing the destination.

## Symptom

The Removed columns step errors because it can't find Ingest_File, and the query's schema no longer matches the destination's fixed schema.

## How it runs here

This lab is a model, not a dataflow. settings.json holds the query's source columns, its applied steps in order, and its data destination's settings. Check my work reads them as data; Run lists every setting.

## What this run proves

- Your query removes the right column by its exact name and gives each column the destination's type, with the destination left as it was, checked against a model on this PC.

## What it doesn't prove

- That the dataflow publishes or refreshes in Fabric: nothing runs, and no Power Query (M) is evaluated here.
- Error codes and messages in the refresh history: the model describes the faults, it doesn't reproduce Fabric's errors.
- Destinations with automatic settings or a dynamic schema, which change the table instead of refusing a mismatch.

# Optimize a pipeline: partitioned copy, copy parallelism, throughput and no waits

A one-time backfill copies 1.5 billion orders from Azure SQL Database into the lakehouse, then 40 small reference tables. A test run copied orders in one session and took over two hours, and the team also burned capacity on tables that take minutes. The database team allows 16 sessions at most.

## What you'll do

Speed up a one-time load and cut its cost: split a large copy with a dynamic range, cap its parallel copies at the source's limit, remove waits that do nothing, and spend less throughput on the small tables.

## How it runs here

This lab is a model, not Fabric. settings.json holds what the backfill needs and a cut-down pipeline you change. The Copy settings use the names from the Copy activity's Source and Settings tabs. Check my work reads the file as data. Run lists every setting so you can see what the checks read.

## What this run proves

- Your settings split the orders copy by order_id into 16 parallel copies with known bounds, drop the waits, and run the small tables after it in parallel batches at the lowest named throughput, as the SQL copy performance and Copy activity pages describe them, checked against a model on this PC.

## What it doesn't prove

- That the pipeline runs or gets faster. Nothing runs here, and no time is measured.
- How many parallel copies or how much throughput the service really uses. The copy output shows those after a real run.
- The rest of a real pipeline definition. This cut-down model uses the settings' names from the Copy activity's tabs, not its JSON.

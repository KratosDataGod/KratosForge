# Copy activity design: staging, schema mapping and fault tolerance

A nightly pipeline copies stores' sales files from ADLS Gen2 into a Warehouse. The first runs created a new table with the files' column names, piled every night on top of the last, and either failed when a file vanished or quietly skipped files nobody was allowed to read.

## What you'll do

Set up one copy activity into a Warehouse: decide on staging, map the columns into an existing table, clear it before each load, and choose which file errors to skip and log.

## How it runs here

This lab is a model, not Fabric. settings.json holds what each team needs and the settings you choose. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## What this run proves

- Your staging, table option, mapping, pre-copy script and fault tolerance meet the five needs, as the copy activity and Warehouse destination pages on Learn describe them, checked against a model on this PC.

## What it doesn't prove

- That any file is read or any row written: nothing is copied.
- Throughput, parallel copies and the other settings this model leaves out.
- Which errors each connector can skip, and the exact layout of the copy activity's tabs.

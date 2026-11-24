# Compact a partitioned Delta table and keep time travel

Freezer readings land in 12 small batches a day, and each batch is appended to its day's partition. After four days the table has 48 data files, and readers who filter by day open 12 files for every day they read. Support also needs time travel. When a reading looks wrong, they compare the table with an earlier version, so the files behind the last 7 days of versions must stay.

## What you'll do

Compact a partitioned Delta table to one file per partition with OPTIMIZE, clean up unused files with VACUUM at the default retention, and keep the table's partitioning, rows and time travel.

## How it runs here

Your answer is a Python script. It runs on this PC with delta-rs (the `deltalake` package) and Polars, in the exercise's own folder, with no sign-in and no network. A Fabric lakehouse stores tables in the same Delta format, but there you'd usually run OPTIMIZE and VACUUM from a notebook or from the lakehouse's table maintenance.

## Before you start

- E56

## What this run proves

- Your script ran on this PC and left a Delta table still partitioned by reading_date, with one data file per day, an OPTIMIZE in its history after the writes, and all 1,200 readings once each.
- Version 0 of the table can still be read after your maintenance, and it holds the first batch of 25 readings.

## What it doesn't prove

- That VACUUM ran. delta-rs adds nothing to the table's history when a vacuum has no file to delete, so a script that skips it passes too. What is checked is that time travel still works afterwards.
- Read speed in Fabric. This checks file counts in the Delta log, not query timings.
- V-Order, Z-Order and the lakehouse's table maintenance in Fabric, which do this from the lakehouse or a notebook.
- That the table is a lakehouse table in OneLake. Here it's a Delta table in the exercise folder on this PC.

# File layout: partition a Delta table so queries can skip files

Finance reads sales one month at a time, and the table will hold years of data. The first version was written as one undivided table. Write it so a month's query can skip every other month's files, without making thousands of tiny folders.

## What you'll do

Choose a partition column that matches how a table is read and has few distinct values, and write the Delta table with it.

## How it runs here

Your answer is a Python script. It runs on this PC with delta-rs (the `deltalake` package) and Polars, in the exercise's own folder, with no sign-in and no network. A Fabric lakehouse stores tables in the same Delta format, but there you'd usually write this with Spark in a notebook.

## Before you start

- LM-27

## What this run proves

- Your script ran on this PC and wrote a Delta table partitioned by sale_month, one partition per month, with all 600 sales once each.

## What it doesn't prove

- How Spark in Fabric prunes partitions or uses file statistics when it reads: this checks the table's layout in its log, not a query plan.
- Whether partitioning is worth it at your real data size: small tables often read faster unpartitioned.

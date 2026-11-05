# Incremental MERGE into a Delta table

silver_orders is a Delta table loaded once a day. Day 2's file holds every order that changed since day 1, plus new orders. The current job appends it, so changed orders appear twice, and a retry doubles everything again.

## What you'll do

Load a day's changes into a Delta table with MERGE: update the rows that changed, insert the new ones, and make a retried load change nothing.

## How it runs here

Your answer is a Python script. It runs on this PC with delta-rs (the `deltalake` package) and Polars, in the exercise's own folder, with no sign-in and no network. A Fabric lakehouse stores tables in the same Delta format, but there you'd usually write this with Spark in a notebook.

## Before you start

- LM-11
- LM-21

## What this run proves

- Your script ran on this PC and left a Delta table whose log shows a WRITE then a MERGE, with one row per order and day 2's changes applied, even after the retry.

## What it doesn't prove

- Spark's MERGE INTO in a Fabric notebook: this used delta-rs. The Delta log format is the same, but the API, performance and concurrency rules differ.
- Writing to OneLake or a lakehouse's Tables folder.

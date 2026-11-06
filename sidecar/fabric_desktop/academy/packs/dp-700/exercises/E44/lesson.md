# Monitoring hub: find the failed ingestion and the run that got slower

Every morning someone opens the monitoring hub and scrolls through two pipelines and two notebooks to see what failed overnight and what took too long. You have the same run history as a table. Turn the scroll into one query that a failure or a slow run can't hide from.

## What you'll do

From a run history like the monitoring hub's, write the morning check: each item's latest run, its error if it failed, its usual duration, and whether it got slower.

## How it runs here

The monitoring hub lists runs of pipelines, notebooks and other items with their status and start time, and a run's details show its error. This lab gives you a made-up history shaped like that list as a table, run_history, so the reasoning can be checked here.

## What this run proves

- Your query finds each item's latest run by its start time, reports the failure and its error, and flags the slower run against a median of earlier successful runs, on a simulated run history with DuckDB on this PC.

## What it doesn't prove

- The monitoring hub itself: its filters, columns, and the run details and error pages you'd open from it. The run history here is made up and shaped like what it lists.
- Where Fabric keeps run history, for how long, and how to query it there.
- Alerts: sending this result to someone is objective 3.1.4.

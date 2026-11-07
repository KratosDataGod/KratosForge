# Capacity Metrics: find the throttled hour and what caused it

Report users on an F64 capacity saw slow visuals in the morning and errors after lunch. The Capacity Metrics app's throttling charts show when; you need to say which stage, and what caused it. You have the day's operations and the carried-forward overage per timepoint. Name the culprit without blaming whatever was running at the time.

## What you'll do

From a Capacity Metrics-style timeline, find the hours an F64 capacity was throttled, the worst stage each reached, and the item that used the most smoothed capacity in each.

## How it runs here

The Capacity Metrics app shows a capacity's use in timepoints of 30 seconds, after smoothing, and its throttling charts show interactive delay, interactive rejection and background rejection over time. Drilling into a timepoint lists the interactive and background operations that add to it.

This lab gives you a made-up day on an F64 (64 CU, so 230,400 CU seconds an hour) as two tables: operations, and throttle_log with the carried-forward overage per 5-minute timepoint. Smoothing is done by whole hours, as the starter query explains.

## What this run proves

- Your query finds the throttled hours from the carried-forward overage, names the worst stage in each, and ranks items by smoothed CU, background over 24 hours and interactive in its own hour, on simulated data with DuckDB on this PC.

## What it doesn't prove

- The Capacity Metrics app itself: its pages, charts, drill-through to a timepoint, and 30-second timepoints. The operations and timeline here are made up and shaped like what it shows.
- Fabric's exact smoothing, which starts at the operation's own timepoint rather than a whole hour, and smooths interactive operations over at least 5 minutes.
- What to do about it: scaling, pausing, moving workloads or surge protection.

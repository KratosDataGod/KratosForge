# Optimize an eventhouse: caching, retention, materialized views and query shape

Fernbrook stores sensor readings in an eventhouse. Dashboards look at recent days, auditors want old data gone after three years, and the same aggregation runs on every tile.

## What you'll do

Choose the eventhouse policies and query patterns that keep recent queries fast and old data under control.

## Notes

- The caching policy decides which recent data stays hot on local SSD.
- The retention policy's soft-delete period decides how long data is kept at all.
- A materialized view returns an up-to-date aggregation more cheaply than running it over the source table.
- In KQL, filter on the datetime column first, and use has rather than contains for whole terms.

## What this run proves

- You can pick the right setting or pattern for each scenario, with the reason and source shown afterwards.

## What it doesn't prove

- That you can apply it in Fabric: nothing is set up or run here. Doing it on a real item is FABRIC VERIFIED work.

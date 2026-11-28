# Optimize an eventstream and an eventhouse: throughput, retention, caching and a materialized view

A fleet telemetry solution works, but it's slow and costly. The eventstream is set for under 10 MB/s while trucks send up to 40 MB/s. Every table keeps all its data hot forever, including a staging table nobody reads. And a dashboard tile scans all of Telemetry every 30 seconds to find each truck's latest reading.

## What you'll do

Fit an eventstream's throughput and retention to its workload, keep a staging table empty with a zero retention period, cache only the data dashboards read, and serve a latest-value tile from a materialized view.

## How it runs here

This lab is a model, not Fabric. settings.json holds what the solution needs, the eventstream's settings, the KQL database's retention and caching policies, its materialized views and the tile's query. Retention has a SoftDeletePeriod_days value and Recoverability. Caching has a hot_days value. A null means no policy is set there. Name the view LatestByTruck, and describe it with source, aggregation (such as arg_max) and by. Check my work reads the file as data. Run lists every setting so you can see what the checks read.

## Things to know

- Event throughput has three levels. Low is under 10 MB/s, Medium is 10 to 100 MB/s and High is over 100 MB/s.
- Retention decides how long data stays queryable. Caching decides which part of it is hot, on fast local storage.
- A table's policy overrides the database's policy.
- In Kusto, with no policy set, data is kept for 1,000 years and all of it is hot. In Fabric, a KQL database keeps data for 3,650 days and caches it for 3,650 days by default. This model starts with no policy set.
- materialized_view("Name") reads only a view's materialized part. It can be a little behind, and it's faster than reading the whole view.

## What this run proves

- Your settings pick the throughput level that covers 40 MB/s, keep events 3 to 7 days, give the staging table a zero retention period with recoverability off behind a transactional update policy, keep Telemetry a year with 30 days hot, leave the database's policies alone, and serve the tile from the materialized part of an arg_max view, as the eventstream settings and Kusto policy pages describe them, checked against a model on this PC.

## What it doesn't prove

- That anything gets faster or cheaper. Nothing runs here, and no time or capacity is measured.
- That an eventhouse accepts the policies or creates the view. No KQL runs here, and the model uses whole days instead of KQL timespans.
- The cost of each throughput level or retention period. The settings page gives the levels' rates, not their prices.

## Read more on Learn

- [Configure settings for an eventstream](https://learn.microsoft.com/fabric/real-time-intelligence/event-streams/configure-settings)
- [Retention policy](https://learn.microsoft.com/kusto/management/retention-policy?view=microsoft-fabric)
- [Caching policy](https://learn.microsoft.com/kusto/management/cache-policy?view=microsoft-fabric)
- [Change data policies](https://learn.microsoft.com/fabric/real-time-intelligence/data-policies)
- [Materialized views](https://learn.microsoft.com/kusto/management/materialized-views/materialized-view-overview?view=microsoft-fabric)

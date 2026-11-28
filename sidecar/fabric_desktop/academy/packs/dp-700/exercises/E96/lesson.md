# KQL in SQL: where, extend, summarize by bin, countif and sort

An eventstream lands web requests in an Eventhouse table, AppRequests. The on-call team runs a KQL query each morning that lists, for the eu-west region, every 15-minute window in which a service returned server errors. A new report has to show the same rows, and the report tool only speaks SQL. Your SQL must return what the KQL query returns, row for row and in the same order.

## What you'll do

Read a KQL query that finds 15-minute windows with server errors, and write the SQL that returns exactly what the KQL query returns. That means the case-sensitive filter, bin rounding down, count and countif, the columns summarize gives, the filter after summarize and KQL's default sort order.

## The KQL query

```kusto
AppRequests
| where Region == "eu-west"
| extend IsError = StatusCode >= 500
| summarize Requests = count(), Errors = countif(IsError), MaxDurationMs = max(DurationMs)
    by Service, TimeBin = bin(Timestamp, 15m)
| extend ErrorPct = round(100.0 * Errors / Requests, 1)
| where Errors > 0
| order by TimeBin, Service asc
```

## What each step does

A KQL query is a table followed by steps joined with a pipe. Each step takes the rows from the step before it.

- **where** keeps the rows where the condition is true. The == operator is case sensitive. Use =~ for a comparison that ignores case.
- **extend** adds a calculated column and keeps all the others.
- **summarize** groups the rows by the columns after by and computes the aggregates for each group. The result has one row per combination of by values, with the by columns first and then one column per aggregate. A bin with no rows doesn't appear.
- **bin(Timestamp, 15m)** rounds a value down to a multiple of the bin size, so 09:14:59 falls in 09:00 and 09:15:00 in 09:15.
- **count()** counts the rows in the group. **countif(condition)** counts the rows where the condition is true. **max** ignores nulls.
- **round(value, 1)** rounds to one decimal place.
- **where** after summarize filters the groups, not the original rows.
- **order by** sorts. With no direction it sorts descending. The direction applies to each column on its own, so here TimeBin is newest first and Service is A to Z.

Sources on Microsoft Learn:

- [summarize operator](https://learn.microsoft.com/en-us/kusto/query/summarize-operator)
- [bin()](https://learn.microsoft.com/en-us/kusto/query/bin-function)
- [countif()](https://learn.microsoft.com/en-us/kusto/query/countif-aggregation-function)
- [String operators](https://learn.microsoft.com/en-us/kusto/query/datatypes-string-operators)
- [sort operator](https://learn.microsoft.com/en-us/kusto/query/sort-operator)
- [round()](https://learn.microsoft.com/en-us/kusto/query/round-function)
- [Query data in a KQL queryset](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/kusto-query-set)

## How it runs here

There's no KQL engine on this PC, and this lab doesn't pretend there is one. You write SQL, and DuckDB runs it over a made-up AppRequests table shaped like one an eventstream fills. The check compares your rows with a SQL reference written by the KQL rules above. In DuckDB, time_bucket(INTERVAL 15 MINUTE, Timestamp) rounds down like bin, and count(*) FILTER (WHERE ...) counts like countif. In Fabric, you would run the KQL query itself in a KQL queryset.

## What this run proves

- Your SQL returns the rows the stated KQL query would return, by the KQL rules the lesson quotes from Learn, in the same columns and order, on simulated data with DuckDB on this PC.

## What it doesn't prove

- Any KQL. Nothing here runs KQL, so a KQL query you write isn't checked. The expected rows come from a SQL reference written by those rules.
- Eventhouse behavior such as ingestion, caching or retention, or how fast the query runs.
- KQL functions the query doesn't use, and bins with no rows, which summarize doesn't return.

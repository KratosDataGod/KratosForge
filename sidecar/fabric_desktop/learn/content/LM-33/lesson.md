# Capacity and cost

A Fabric capacity is bought as a number of capacity units (CUs); every job, refresh and query
uses CU-seconds from it. Short peaks are smoothed over time, but a capacity that is busy for
long throttles everyone on it. The Capacity Metrics app reports usage by item and operation.

## Find the big users first

Cost work starts with a ranked list: a handful of items usually use most of the capacity, and
fixing one of them beats tuning twenty small ones. Show each one's share of the total so the
order is obvious:

```sql
WITH week AS (
    SELECT item, sum(cu_seconds) AS cu_seconds
    FROM cu_usage
    WHERE day >= DATE '2026-10-26' AND day < DATE '2026-11-02'
    GROUP BY item
)
SELECT item, cu_seconds, round(100.0 * cu_seconds / (SELECT sum(cu_seconds) FROM week), 1) AS share_pct
FROM week ORDER BY cu_seconds DESC LIMIT 3;
```

Running development on this PC instead of in Fabric uses no capacity for the compute; reads and
writes to OneLake still do. KratosForge's risk check estimates a job's cost before it runs.

## Try it

Rank last week's three biggest users of the capacity.

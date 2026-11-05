# Dates, times and time zones

A date is a day; a timestamp is a moment. Most date bugs come from mixing them up, or from
forgetting which time zone a timestamp is in.

## Store UTC, report local

Store moments in UTC, so every system agrees on them, and turn them into local days only when you
report. A local day is a range of UTC moments: 31 October 2026 in New York (UTC-4 that day) runs
from 04:00 UTC on the 31st to 04:00 UTC on 1 November. Daylight saving moves that offset twice a
year, so take it from a time zone table or the platform's time zone functions, never a constant
you guessed. In Fabric Warehouse, `AT TIME ZONE` converts; in DuckDB, `timezone()` does.

## Half-open ranges

Filter with a start you include and an end you don't:

```sql
WHERE sold_at >= TIMESTAMP '2026-10-31 04:00'
  AND sold_at <  TIMESTAMP '2026-11-01 04:00'
```

`BETWEEN` includes both ends, so a sale at exactly 04:00 on 1 November would count twice, once
for each day. And `CAST(sold_at AS DATE) = ...` turns every row's timestamp into a date before
comparing: it picks the UTC day, and rule SQ002 flags it because the engine can't skip blocks of
rows by their timestamps once a function wraps the column.

## Try it

Total the sales of 31 October 2026 in New York.

-- Write the SQL that returns what this KQL query returns in the Eventhouse:
--
--   AppRequests
--   | where Region == "eu-west"
--   | extend IsError = StatusCode >= 500
--   | summarize Requests = count(), Errors = countif(IsError), MaxDurationMs = max(DurationMs)
--       by Service, TimeBin = bin(Timestamp, 15m)
--   | extend ErrorPct = round(100.0 * Errors / Requests, 1)
--   | where Errors > 0
--   | order by TimeBin, Service asc
--
-- Columns, in the order KQL gives them: Service, TimeBin, Requests, Errors, MaxDurationMs, ErrorPct.
-- The lesson says what each KQL step does. Mind the comparison, the bin and the sort.
SELECT Timestamp, Service, Region, StatusCode, DurationMs
FROM AppRequests
ORDER BY Timestamp;

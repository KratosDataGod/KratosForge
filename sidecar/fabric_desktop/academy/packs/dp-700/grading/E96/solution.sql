-- where Region == "eu-west" is case sensitive. bin() rounds down to a multiple of 15 minutes.
-- countif(IsError) counts the rows where StatusCode >= 500 is true (a NULL status isn't).
-- order by with no direction sorts descending in KQL, so TimeBin is newest first, then Service ascending.
WITH grouped AS (
    SELECT Service,
           time_bucket(INTERVAL 15 MINUTE, Timestamp) AS TimeBin,
           count(*) AS Requests,
           count(*) FILTER (WHERE StatusCode >= 500) AS Errors,
           max(DurationMs) AS MaxDurationMs
    FROM AppRequests
    WHERE Region = 'eu-west'
    GROUP BY Service, time_bucket(INTERVAL 15 MINUTE, Timestamp)
)
SELECT Service, TimeBin, Requests, Errors, MaxDurationMs,
       round(100.0 * Errors / Requests, 1) AS ErrorPct
FROM grouped
WHERE Errors > 0
ORDER BY TimeBin DESC, Service ASC;

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
ORDER BY TimeBin ASC, Service ASC;

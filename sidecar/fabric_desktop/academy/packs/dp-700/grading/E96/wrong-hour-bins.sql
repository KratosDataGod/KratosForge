WITH grouped AS (
    SELECT Service,
           date_trunc('hour', Timestamp) AS TimeBin,
           count(*) AS Requests,
           count(*) FILTER (WHERE StatusCode >= 500) AS Errors,
           max(DurationMs) AS MaxDurationMs
    FROM AppRequests
    WHERE Region = 'eu-west'
    GROUP BY Service, date_trunc('hour', Timestamp)
)
SELECT Service, TimeBin, Requests, Errors, MaxDurationMs,
       round(100.0 * Errors / Requests, 1) AS ErrorPct
FROM grouped
WHERE Errors > 0
ORDER BY TimeBin DESC, Service ASC;

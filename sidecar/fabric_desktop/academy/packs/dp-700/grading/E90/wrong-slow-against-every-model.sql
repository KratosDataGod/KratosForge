WITH runs AS (
    SELECT model_name, status, error_type, start_time,
           date_diff('second', start_time, end_time) AS seconds,
           row_number() OVER (PARTITION BY model_name ORDER BY start_time DESC) AS rn
    FROM refresh_history
),
latest AS (
    SELECT model_name, status, error_type FROM runs WHERE rn = 1
),
streak AS (
    SELECT r.model_name,
           coalesce(min(r.rn) FILTER (WHERE r.status = 'Completed') - 1, count(*)) AS failures
    FROM runs AS r
    GROUP BY r.model_name
),
good AS (
    SELECT model_name, max(start_time) AS last_good_start,
           median(seconds) AS median_seconds
    FROM runs
    WHERE status = 'Completed'
    GROUP BY model_name
),
slow AS (
    SELECT r.model_name, count(*) FILTER (WHERE r.seconds > 2 * g.median_seconds) AS n
    FROM runs AS r
    CROSS JOIN (SELECT median(seconds) AS median_seconds FROM runs WHERE status = 'Completed') AS g
    WHERE r.status = 'Completed'
    GROUP BY r.model_name
)
SELECT m.model_name, m.storage_mode, l.status AS last_status, g.last_good_start,
       s.failures AS consecutive_failures,
       (s.failures >= 4 OR (l.status = 'Failed' AND l.error_type = 'Credentials')) AS schedule_disabled,
       coalesce(sl.n, 0) AS slow_refreshes
FROM models AS m
JOIN latest AS l ON l.model_name = m.model_name
JOIN streak AS s ON s.model_name = m.model_name
LEFT JOIN good AS g ON g.model_name = m.model_name
LEFT JOIN slow AS sl ON sl.model_name = m.model_name
ORDER BY m.model_name;

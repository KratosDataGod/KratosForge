WITH latest AS (
    SELECT activity_name, run_start, status, rows_copied, rows_skipped, error_message,
           row_number() OVER (PARTITION BY activity_name ORDER BY run_start DESC) AS rn
    FROM copy_runs
)
SELECT a.activity_name, l.run_start AS last_start, l.status AS last_status, l.rows_copied, l.rows_skipped,
       CASE WHEN l.run_start IS NULL OR l.run_start < k.checked_at - INTERVAL 24 HOUR THEN 'not run'
            WHEN l.status = 'Failed' THEN 'failed'
            WHEN l.rows_copied = 0 THEN 'no rows'
            WHEN l.rows_skipped > 0 THEN 'rows skipped'
            ELSE 'ok' END AS issue,
       l.error_message AS last_error
FROM copy_activities AS a
CROSS JOIN ingestion_check AS k
LEFT JOIN latest AS l ON l.activity_name = a.activity_name AND l.rn = 1
ORDER BY a.activity_name;

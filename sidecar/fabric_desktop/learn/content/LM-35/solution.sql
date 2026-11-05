WITH latest AS (
    SELECT job, status, row_number() OVER (PARTITION BY job ORDER BY started_at DESC) AS n
    FROM job_runs
),
last_ok AS (
    SELECT job, max(started_at) AS ok_at FROM job_runs WHERE status = 'succeeded' GROUP BY job
)
SELECT l.job, CASE WHEN l.status = 'failed' THEN 'failed' ELSE 'stale' END AS reason
FROM latest AS l
LEFT JOIN last_ok AS o ON o.job = l.job
WHERE l.n = 1 AND (l.status = 'failed' OR o.ok_at IS NULL OR o.ok_at < TIMESTAMP '2026-11-01 12:00')
ORDER BY l.job;

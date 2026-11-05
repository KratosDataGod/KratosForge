WITH week AS (
    SELECT item, sum(cu_seconds) AS cu_seconds
    FROM cu_usage
    WHERE day >= DATE '2026-10-26' AND day < DATE '2026-11-02'
    GROUP BY item
)
SELECT item, cu_seconds,
       round(100.0 * cu_seconds / (SELECT sum(cu_seconds) FROM week), 1) AS share_pct
FROM week
ORDER BY cu_seconds DESC
LIMIT 3;

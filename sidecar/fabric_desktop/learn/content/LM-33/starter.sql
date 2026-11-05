SELECT item, sum(cu_seconds) AS cu_seconds
FROM cu_usage
GROUP BY item
ORDER BY cu_seconds DESC
LIMIT 3;

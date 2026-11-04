SELECT file_name
FROM file_stats
WHERE min_date <= DATE '2026-10-15' AND max_date >= DATE '2026-10-15'
ORDER BY file_name;

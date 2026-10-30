SELECT version, sum(num_records) AS row_count
FROM delta_log
WHERE action = 'add'
GROUP BY version
ORDER BY version;

SELECT path, num_records
FROM delta_log
WHERE action = 'add' AND version <= 2
ORDER BY path;

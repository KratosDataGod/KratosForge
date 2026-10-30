SELECT v.version, sum(a.num_records) AS row_count
FROM (SELECT DISTINCT version FROM delta_log) AS v
JOIN delta_log AS a ON a.action = 'add' AND a.version <= v.version
WHERE NOT EXISTS (SELECT 1 FROM delta_log AS r
                  WHERE r.action = 'remove' AND r.path = a.path AND r.version <= v.version)
GROUP BY v.version
ORDER BY v.version;

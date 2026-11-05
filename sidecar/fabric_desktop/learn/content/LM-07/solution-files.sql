SELECT a.path, a.num_records
FROM delta_log AS a
WHERE a.action = 'add' AND a.version <= 2
  AND NOT EXISTS (SELECT 1 FROM delta_log AS r
                  WHERE r.action = 'remove' AND r.path = a.path AND r.version <= 2)
ORDER BY a.path;

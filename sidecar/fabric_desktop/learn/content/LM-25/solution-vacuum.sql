SELECT file_name
FROM delta_files
WHERE removed_on IS NOT NULL AND removed_on < DATE '2026-10-26'
ORDER BY file_name;

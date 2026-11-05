SELECT file_name FROM delta_files WHERE removed_on IS NOT NULL ORDER BY file_name;

SELECT count(*) AS small_files FROM delta_files WHERE removed_on IS NULL AND size_mb < 32;

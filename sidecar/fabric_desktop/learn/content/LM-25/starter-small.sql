SELECT count(*) AS small_files FROM delta_files WHERE size_mb < 32;

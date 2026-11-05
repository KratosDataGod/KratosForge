-- A small model of a Delta table's log (_delta_log): each commit adds or removes whole Parquet files.
CREATE TABLE delta_log AS
SELECT * FROM (VALUES
    (0, 'add',    'part-000.parquet', 500),
    (1, 'add',    'part-001.parquet', 20),
    (2, 'add',    'part-002.parquet', 15),
    (2, 'remove', 'part-000.parquet', 500),
    (2, 'add',    'part-003.parquet', 480),
    (3, 'remove', 'part-001.parquet', 20),
    (3, 'remove', 'part-002.parquet', 15),
    (3, 'remove', 'part-003.parquet', 480),
    (3, 'add',    'part-004.parquet', 515)
) AS t(version, action, path, num_records);

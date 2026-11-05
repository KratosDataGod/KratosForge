CREATE TABLE file_stats (file_name VARCHAR, min_date DATE, max_date DATE);
INSERT INTO file_stats VALUES
    ('f1.parquet', DATE '2026-10-01', DATE '2026-10-31'),
    ('f2.parquet', DATE '2026-10-01', DATE '2026-10-31'),
    ('f3.parquet', DATE '2026-10-01', DATE '2026-10-10'),
    ('f4.parquet', DATE '2026-10-11', DATE '2026-10-20'),
    ('f5.parquet', DATE '2026-10-21', DATE '2026-10-31');

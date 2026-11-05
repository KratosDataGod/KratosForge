CREATE TABLE delta_files (file_name VARCHAR, size_mb INTEGER, added_on DATE, removed_on DATE);
INSERT INTO delta_files VALUES
    ('part-001.parquet', 4, DATE '2026-10-01', DATE '2026-10-10'),
    ('part-002.parquet', 6, DATE '2026-10-01', DATE '2026-10-10'),
    ('part-003.parquet', 256, DATE '2026-10-10', NULL),
    ('part-004.parquet', 3, DATE '2026-10-20', DATE '2026-10-30'),
    ('part-005.parquet', 2, DATE '2026-10-28', NULL),
    ('part-006.parquet', 5, DATE '2026-10-29', NULL),
    ('part-007.parquet', 9, DATE '2026-10-30', NULL),
    ('part-008.parquet', 128, DATE '2026-10-30', NULL);

CREATE TABLE job_runs (job VARCHAR, started_at TIMESTAMP, status VARCHAR);
INSERT INTO job_runs VALUES
    ('load_sales', TIMESTAMP '2026-11-02 06:00', 'succeeded'),
    ('load_stock', TIMESTAMP '2026-11-01 06:00', 'succeeded'),
    ('load_stock', TIMESTAMP '2026-11-02 06:00', 'failed'),
    ('build_gold', TIMESTAMP '2026-10-31 08:00', 'failed'),
    ('build_gold', TIMESTAMP '2026-11-01 08:00', 'succeeded'),
    ('refresh_model', TIMESTAMP '2026-11-02 07:00', 'succeeded'),
    ('refresh_model', TIMESTAMP '2026-11-02 09:00', 'failed');

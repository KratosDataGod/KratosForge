-- The pipeline's run history for the sales load.
CREATE TABLE load_log (run_id VARCHAR, batch_id VARCHAR, started_at TIMESTAMP, status VARCHAR);
INSERT INTO load_log VALUES
    ('r1', 'b1', TIMESTAMP '2026-05-10 02:00:00', 'succeeded'),
    ('r2', 'b2', TIMESTAMP '2026-05-11 02:00:00', 'failed'),
    ('r3', 'b2', TIMESTAMP '2026-05-11 02:05:00', 'succeeded');

CREATE TABLE silver_sales (sale_id INTEGER, amount DECIMAL(12,2), batch_id VARCHAR, run_id VARCHAR);
INSERT INTO silver_sales VALUES
    (1, 10.00, 'b1', 'r1'), (2, 20.00, 'b1', 'r1'),
    (3, 30.00, 'b2', 'r2'), (4, 40.00, 'b2', 'r2'),
    (3, 30.00, 'b2', 'r3'), (4, 40.00, 'b2', 'r3');

-- Today's batch: sale 4's amount was corrected, sale 5 is new.
CREATE TABLE stg_batch_b3 (sale_id INTEGER, amount DECIMAL(12,2));
INSERT INTO stg_batch_b3 VALUES (4, 42.00), (5, 50.00);

-- Repairs the table but keeps the append, so the retry doubles b3.
DELETE FROM silver_sales WHERE run_id IN (SELECT run_id FROM load_log WHERE status = 'failed');
INSERT INTO silver_sales SELECT sale_id, amount, 'b3', 'r4' FROM stg_batch_b3;
INSERT INTO silver_sales SELECT sale_id, amount, 'b3', 'r5' FROM stg_batch_b3;

SELECT sale_id, amount, batch_id FROM silver_sales ORDER BY sale_id;

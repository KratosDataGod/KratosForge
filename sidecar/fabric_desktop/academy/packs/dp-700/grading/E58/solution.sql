DELETE FROM silver_sales
WHERE run_id IN (SELECT run_id FROM load_log WHERE status = 'failed');

MERGE INTO silver_sales AS t
USING (SELECT sale_id, amount, 'b3' AS batch_id, 'r4' AS run_id FROM stg_batch_b3) AS s ON t.sale_id = s.sale_id
WHEN MATCHED THEN UPDATE SET amount = s.amount, batch_id = s.batch_id, run_id = s.run_id
WHEN NOT MATCHED THEN INSERT (sale_id, amount, batch_id, run_id) VALUES (s.sale_id, s.amount, s.batch_id, s.run_id);

MERGE INTO silver_sales AS t
USING (SELECT sale_id, amount, 'b3' AS batch_id, 'r5' AS run_id FROM stg_batch_b3) AS s ON t.sale_id = s.sale_id
WHEN MATCHED THEN UPDATE SET amount = s.amount, batch_id = s.batch_id, run_id = s.run_id
WHEN NOT MATCHED THEN INSERT (sale_id, amount, batch_id, run_id) VALUES (s.sale_id, s.amount, s.batch_id, s.run_id);

SELECT sale_id, amount, batch_id FROM silver_sales ORDER BY sale_id;

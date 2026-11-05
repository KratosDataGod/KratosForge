-- 1. Repair silver_sales.
-- 2. Repair this load step, then leave it here twice (run r4 and its retry r5) for batch b3.

INSERT INTO silver_sales SELECT sale_id, amount, 'b3', 'r4' FROM stg_batch_b3;


SELECT sale_id, amount, batch_id FROM silver_sales ORDER BY sale_id;

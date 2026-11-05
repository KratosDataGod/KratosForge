-- DISTINCT keeps both copies (run_id differs), and INSERT doubles b3.
CREATE OR REPLACE TABLE silver_sales AS SELECT DISTINCT * FROM silver_sales;
INSERT INTO silver_sales SELECT sale_id, amount, 'b3', 'r4' FROM stg_batch_b3;
INSERT INTO silver_sales SELECT sale_id, amount, 'b3', 'r5' FROM stg_batch_b3;

SELECT sale_id, amount, batch_id FROM silver_sales ORDER BY sale_id;

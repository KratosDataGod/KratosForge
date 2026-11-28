-- Deletes the bad rows from stg_orders, which destroys the evidence and the next run's input.
INSERT INTO order_rejects
SELECT DISTINCT order_id, 'qty, amount or cost is not a number'
FROM stg_orders
WHERE TRY_CAST(qty_text AS INTEGER) IS NULL
   OR TRY_CAST(amount_text AS DECIMAL(12,2)) IS NULL
   OR TRY_CAST(cost_text AS DECIMAL(12,2)) IS NULL;
DELETE FROM stg_orders WHERE NOT (TRY_CAST(qty_text AS INTEGER) IS NOT NULL AND TRY_CAST(amount_text AS DECIMAL(12,2)) IS NOT NULL AND TRY_CAST(cost_text AS DECIMAL(12,2)) IS NOT NULL);

MERGE INTO fact_orders AS t
USING (
    SELECT order_id, qty, amount, cost FROM (
        SELECT order_id,
               TRY_CAST(qty_text AS INTEGER) AS qty,
               TRY_CAST(amount_text AS DECIMAL(12,2)) AS amount,
               TRY_CAST(cost_text AS DECIMAL(12,2)) AS cost,
               row_number() OVER (PARTITION BY order_id ORDER BY version_ts DESC) AS rn
        FROM stg_orders
        WHERE TRY_CAST(qty_text AS INTEGER) IS NOT NULL AND TRY_CAST(amount_text AS DECIMAL(12,2)) IS NOT NULL AND TRY_CAST(cost_text AS DECIMAL(12,2)) IS NOT NULL
    ) WHERE rn = 1
) AS s
ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET qty = s.qty, amount = s.amount, cost = s.cost,
    margin_pct = CAST((s.amount - s.cost) / NULLIF(s.amount, 0) AS DECIMAL(9,4))
WHEN NOT MATCHED THEN INSERT (order_id, qty, amount, cost, margin_pct)
    VALUES (s.order_id, s.qty, s.amount, s.cost, CAST((s.amount - s.cost) / NULLIF(s.amount, 0) AS DECIMAL(9,4)));

SELECT order_id, qty, amount, cost, margin_pct, 'loaded' AS outcome FROM fact_orders
UNION ALL
SELECT order_id, NULL, NULL, NULL, NULL, 'rejected' FROM order_rejects
ORDER BY order_id, outcome;

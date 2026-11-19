-- The nightly load. It stops with an error before it changes anything.
-- Fix it so it loads every order it can, sends the rest to order_rejects, and never stops on one bad row.

MERGE INTO fact_orders AS t
USING (
    SELECT order_id,
           CAST(qty_text AS INTEGER) AS qty,
           CAST(amount_text AS DECIMAL(12,2)) AS amount,
           CAST(cost_text AS DECIMAL(12,2)) AS cost
    FROM stg_orders
) AS s
ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET qty = s.qty, amount = s.amount, cost = s.cost,
    margin_pct = CAST((s.amount - s.cost) / s.amount AS DECIMAL(9,4))
WHEN NOT MATCHED THEN INSERT (order_id, qty, amount, cost, margin_pct)
    VALUES (s.order_id, s.qty, s.amount, s.cost, CAST((s.amount - s.cost) / s.amount AS DECIMAL(9,4)));


-- Keep this query last: it's what the check compares.
SELECT order_id, qty, amount, cost, margin_pct, 'loaded' AS outcome FROM fact_orders
UNION ALL
SELECT order_id, NULL, NULL, NULL, NULL, 'rejected' FROM order_rejects
ORDER BY order_id, outcome;

-- Appends the whole source again: every order that was already there is doubled.
INSERT INTO silver_orders SELECT * FROM src_orders;
UPDATE etl_watermark SET high_water = (SELECT max(modified_at) FROM silver_orders) WHERE table_name = 'orders';

SELECT o.order_id, o.status, o.amount, w.high_water
FROM silver_orders AS o, etl_watermark AS w
WHERE w.table_name = 'orders'
ORDER BY o.order_id;

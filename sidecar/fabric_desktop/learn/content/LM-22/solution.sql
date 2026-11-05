SELECT order_id, amount, 'USD' AS currency FROM batch_jan
UNION ALL
SELECT order_id, amount, currency FROM batch_feb
ORDER BY order_id;

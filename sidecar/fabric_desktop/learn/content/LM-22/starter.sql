SELECT * FROM batch_jan
UNION ALL
SELECT * FROM batch_feb
ORDER BY order_id;

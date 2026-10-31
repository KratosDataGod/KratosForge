SELECT region, order_id, amount
FROM orders
QUALIFY row_number() OVER (PARTITION BY region ORDER BY amount DESC, order_id) = 1
ORDER BY region;

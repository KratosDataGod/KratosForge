SELECT order_id, customer, amount
FROM orders
WHERE region = 'North'
  AND amount > 100
ORDER BY amount DESC;

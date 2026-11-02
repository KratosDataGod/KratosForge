CREATE TABLE customer_summary (
    customer_id INTEGER NOT NULL, name VARCHAR, segment VARCHAR,
    orders INTEGER NOT NULL, revenue DECIMAL(12, 2) NOT NULL, last_order DATE
);
INSERT INTO customer_summary (customer_id, name, segment, orders, revenue, last_order)
SELECT c.customer_id, c.name, c.segment, count(o.order_id), coalesce(sum(o.amount), 0), max(o.order_date)
FROM customers AS c
LEFT JOIN orders AS o ON o.customer_id = c.customer_id
GROUP BY c.customer_id, c.name, c.segment;

SELECT * FROM customer_summary ORDER BY customer_id;

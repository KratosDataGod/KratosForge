-- Orders, their lines and their payments, made from fixed hashes (the same rows every time).
-- An order has 1 to 6 lines and 0 to 3 payments; a few orders aren't paid yet.
CREATE TABLE orders AS
SELECT i AS order_id,
       ['North', 'South', 'East', 'West'][1 + CAST(hash(i, 64, 1) % 4 AS INTEGER)] AS region,
       DATE '2026-01-01' + CAST(hash(i, 64, 2) % 90 AS INTEGER) AS order_date
FROM range(1, 60001) AS t(i);

CREATE TABLE order_lines AS
SELECT o.order_id * 10 + n AS line_id, o.order_id,
       CAST(1 + hash(o.order_id, n, 64, 3) % 20000 / 100.0 AS DECIMAL(12,2)) AS amount
FROM orders AS o, range(1, 7) AS t(n)
WHERE n <= 1 + hash(o.order_id, 64, 4) % 6;

-- Each payment pays an even share of the order, rounded to the cent; unpaid orders have none.
CREATE TABLE payments AS
WITH totals AS (SELECT order_id, sum(amount) AS total FROM order_lines GROUP BY order_id),
     counts AS (SELECT order_id, total, CAST(hash(order_id, 64, 5) % 4 AS INTEGER) AS k FROM totals)
SELECT c.order_id * 10 + n AS payment_id, c.order_id,
       CAST(round(c.total / c.k, 2) AS DECIMAL(12,2)) AS amount
FROM counts AS c, range(1, 4) AS t(n)
WHERE n <= c.k;

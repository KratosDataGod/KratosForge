WITH latest AS (
    SELECT *, row_number() OVER (PARTITION BY order_id ORDER BY loaded_at DESC) AS rn
    FROM raw_orders
),
clean AS (
    SELECT order_id, customer_id, amount
    FROM latest
    WHERE rn = 1 AND customer_id IS NOT NULL AND amount > 0
)
SELECT c.region, sum(o.amount) AS revenue
FROM clean AS o
JOIN customers AS c ON c.customer_id = o.customer_id
GROUP BY c.region
ORDER BY c.region;

SELECT coalesce(ch.customer_id, c.customer_id) AS customer_id,
       CASE WHEN ch.customer_id IS NULL THEN c.name ELSE ch.name END AS name,
       CASE WHEN ch.customer_id IS NULL THEN c.city ELSE ch.city END AS city
FROM customers AS c
FULL OUTER JOIN changes AS ch ON ch.customer_id = c.customer_id
WHERE ch.op IS NULL OR ch.op <> 'delete'
ORDER BY customer_id;

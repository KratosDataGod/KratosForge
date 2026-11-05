SELECT customer_id, email
FROM (SELECT customer_id, email,
             row_number() OVER (PARTITION BY customer_id ORDER BY updated_at DESC) AS n
      FROM customer_updates)
WHERE n = 1
ORDER BY customer_id;

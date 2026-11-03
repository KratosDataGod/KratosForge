UPDATE dim_customer AS d
SET city = c.city
FROM customer_changes AS c
WHERE d.customer_id = c.customer_id;

SELECT * FROM dim_customer ORDER BY customer_id, valid_from;

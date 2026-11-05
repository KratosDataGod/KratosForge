-- Overwrites city in place: no history.
UPDATE dim_customer AS d
SET city = c.city, email = c.email
FROM customer_changes AS c
WHERE d.customer_id = c.customer_id AND d.is_current;

INSERT INTO dim_customer
SELECT (SELECT max(customer_key) FROM dim_customer) + row_number() OVER (ORDER BY c.customer_id),
       c.customer_id, c.city, c.email, c.changed_on, NULL, true
FROM customer_changes AS c
WHERE NOT EXISTS (SELECT 1 FROM dim_customer AS d WHERE d.customer_id = c.customer_id);

SELECT * FROM dim_customer ORDER BY customer_key;

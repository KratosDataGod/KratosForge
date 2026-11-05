-- Treats email as type 2 too, so 102 gets a new row it shouldn't.
UPDATE dim_customer AS d
SET valid_to = c.changed_on, is_current = false
FROM customer_changes AS c
WHERE d.customer_id = c.customer_id AND d.is_current AND (d.city <> c.city OR d.email <> c.email);

INSERT INTO dim_customer
SELECT (SELECT max(customer_key) FROM dim_customer) + row_number() OVER (ORDER BY c.customer_id),
       c.customer_id, c.city, c.email, c.changed_on, NULL, true
FROM customer_changes AS c
WHERE NOT EXISTS (SELECT 1 FROM dim_customer AS d WHERE d.customer_id = c.customer_id AND d.is_current);

SELECT * FROM dim_customer ORDER BY customer_key;

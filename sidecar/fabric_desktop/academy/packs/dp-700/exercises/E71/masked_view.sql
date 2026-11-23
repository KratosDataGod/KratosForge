-- What each person sees: one row per user and customer, as that user's SELECT * FROM customers would return it.
-- Columns: user_name, customer_id, full_name, email, phone, card_number, birth_date, credit_limit.
-- Order by user_name, then customer_id.
-- A masked column shows its real value only to a user who holds UNMASK on that column, or CONTROL on the
-- database. Everyone else sees the mask's output. column_masks lists each column's masking function.
SELECT u.user_name, c.*
FROM users AS u
CROSS JOIN customers AS c
ORDER BY u.user_name, c.customer_id;

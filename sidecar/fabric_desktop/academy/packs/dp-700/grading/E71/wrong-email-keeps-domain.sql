WITH sees AS (
    -- Admin, Member and Contributor hold CONTROL on the database by design, and CONTROL includes UNMASK.
    SELECT u.user_name, m.column_name,
           u.workspace_role IN ('Admin', 'Member', 'Contributor')
           OR EXISTS (SELECT 1 FROM unmask_grants AS g
                      WHERE g.user_name = u.user_name AND g.column_name = m.column_name) AS real_value
    FROM users AS u
    CROSS JOIN column_masks AS m
),
s AS (
    SELECT user_name,
           bool_or(real_value) FILTER (WHERE column_name = 'email') AS email,
           bool_or(real_value) FILTER (WHERE column_name = 'phone') AS phone,
           bool_or(real_value) FILTER (WHERE column_name = 'card_number') AS card_number,
           bool_or(real_value) FILTER (WHERE column_name = 'birth_date') AS birth_date,
           bool_or(real_value) FILTER (WHERE column_name = 'credit_limit') AS credit_limit
    FROM sees
    GROUP BY user_name
)
SELECT s.user_name, c.customer_id, c.full_name,
       CASE WHEN s.email THEN c.email ELSE left(c.email, 1) || 'XXX@' || split_part(c.email, '@', 2) END AS email,
       CASE WHEN s.phone THEN c.phone ELSE 'XXX-XXX-' || right(c.phone, 4) END AS phone,
       CASE WHEN s.card_number THEN c.card_number ELSE 'XXXX-XXXX-XXXX-' || right(c.card_number, 4) END AS card_number,
       CASE WHEN s.birth_date THEN c.birth_date ELSE DATE '1900-01-01' END AS birth_date,
       CASE WHEN s.credit_limit THEN c.credit_limit ELSE 0 END AS credit_limit
FROM s
CROSS JOIN customers AS c
ORDER BY s.user_name, c.customer_id;

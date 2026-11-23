-- A made-up warehouse table, the same every time: three customers, the masks set on their columns, the people who
-- query the table with their workspace roles, and the UNMASK grants they hold. Everyone here can SELECT customers.
CREATE TABLE customers (
  customer_id INTEGER,
  full_name VARCHAR(60),
  email VARCHAR(100),
  phone VARCHAR(20),
  card_number VARCHAR(19),
  birth_date DATE,
  credit_limit DECIMAL(10, 2)
);
INSERT INTO customers VALUES
  (1, 'Ava Brooks', 'ava.brooks@contoso.com', '425-555-0137', '4111111111111111', DATE '1988-04-12', 5000.00),
  (2, 'Liam Chen', 'liam.chen@fabrikam.com', '206-555-0199', '5500000000000004', DATE '1975-11-30', 12500.00),
  (3, 'Noor Haddad', 'n.haddad@outlook.com', '312-555-0144', '340000000000009', DATE '1992-07-04', 800.00);

-- The masks, as they were added with ALTER TABLE ... ALTER COLUMN ... ADD MASKED WITH (FUNCTION = '...').
-- customer_id and full_name have no mask.
CREATE TABLE column_masks AS SELECT * FROM (VALUES
  ('email', 'email()'),
  ('phone', 'partial(0,"XXX-XXX-",4)'),
  ('card_number', 'partial(0,"XXXX-XXXX-XXXX-",4)'),
  ('birth_date', 'default()'),
  ('credit_limit', 'default()')
) AS t(column_name, mask_function);

-- The people who query customers, and their role in the workspace that holds the warehouse.
CREATE TABLE users AS SELECT * FROM (VALUES
  ('ana', 'Viewer'),
  ('ben', 'Viewer'),
  ('chloe', 'Contributor'),
  ('dev', 'Viewer'),
  ('eli', 'Member')
) AS t(user_name, workspace_role);

-- UNMASK grants, each on one column of customers.
CREATE TABLE unmask_grants AS SELECT * FROM (VALUES
  ('ben', 'card_number'),
  ('dev', 'email'),
  ('dev', 'phone')
) AS t(user_name, column_name);

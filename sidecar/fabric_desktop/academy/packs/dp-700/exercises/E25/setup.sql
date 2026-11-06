CREATE TABLE dim_customer (customer_key INTEGER, customer_id INTEGER, name VARCHAR, is_inferred BOOLEAN);
INSERT INTO dim_customer VALUES
    (1, 201, 'Ana Ruiz',   false),
    (2, 202, 'Ben Okafor', false),
    (3, 203, 'Chen Wei',   false);

-- Today's sales. Customers 205 and 206 aren't in the CRM export yet.
CREATE TABLE stg_sales (sale_id INTEGER, customer_id INTEGER, amount DECIMAL(12,2));
INSERT INTO stg_sales VALUES
    (9001, 201, 10.00), (9002, 205, 20.00), (9003, 202, 30.00), (9004, 206, 40.00), (9005, 205, 5.00);

CREATE TABLE fact_sales (sale_id INTEGER, customer_key INTEGER, amount DECIMAL(12,2));

-- Tonight's CRM export: customer 205 has arrived. 206 is still on its way.
CREATE TABLE stg_customers_late (customer_id INTEGER, name VARCHAR);
INSERT INTO stg_customers_late VALUES (205, 'Eve Larsen');

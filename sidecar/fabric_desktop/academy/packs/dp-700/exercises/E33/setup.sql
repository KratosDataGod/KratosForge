-- Bronze: every column is text, exactly as the shop's system sent it.
CREATE TABLE raw_orders (order_id VARCHAR, customer_id VARCHAR, order_ts VARCHAR, amount VARCHAR, status VARCHAR);
INSERT INTO raw_orders VALUES
    ('1', 'C1', '2026-04-01 10:00:00', '12.50', ' Complete'),
    ('2', 'C2', '2026-04-01 11:30:00', 'n/a',   'complete'),
    ('3', 'C1', '2026-04-02 09:00:00', '40',    'OPEN '),
    ('4', 'C3', '2026-04-02 12:00:00', '7.25',  'complete'),
    ('4', 'C3', '2026-04-02 12:00:00', '7.25',  'complete');

CREATE TABLE raw_customers (customer_id VARCHAR, region VARCHAR);
INSERT INTO raw_customers VALUES ('C1', 'North'), ('C2', 'South'), ('C3', 'East');

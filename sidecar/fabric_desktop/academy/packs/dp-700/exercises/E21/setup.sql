-- The source system's orders. modified_at changes whenever an order changes.
CREATE TABLE src_orders AS
SELECT order_id, status, CAST(amount AS DECIMAL(12,2)) AS amount, modified_at FROM (VALUES
    (1, 'open',     10.00, TIMESTAMP '2026-03-01 10:00:00'),
    (2, 'complete', 20.00, TIMESTAMP '2026-03-02 09:00:00'),
    (3, 'complete', 30.00, TIMESTAMP '2026-03-01 18:00:00'),
    (4, 'open',     40.00, TIMESTAMP '2026-03-02 11:00:00'),
    (5, 'open',     50.00, TIMESTAMP '2026-03-02 13:30:00')
) AS t(order_id, status, amount, modified_at);

-- Silver after yesterday's load. Order 2 was still open then.
CREATE TABLE silver_orders AS
SELECT order_id, status, CAST(amount AS DECIMAL(12,2)) AS amount, modified_at FROM (VALUES
    (1, 'open',     10.00, TIMESTAMP '2026-03-01 10:00:00'),
    (2, 'open',     20.00, TIMESTAMP '2026-03-01 12:00:00'),
    (3, 'complete', 30.00, TIMESTAMP '2026-03-01 18:00:00')
) AS t(order_id, status, amount, modified_at);

-- The last change yesterday's load took.
CREATE TABLE etl_watermark (table_name VARCHAR PRIMARY KEY, high_water TIMESTAMP);
INSERT INTO etl_watermark VALUES ('orders', TIMESTAMP '2026-03-01 18:00:00');

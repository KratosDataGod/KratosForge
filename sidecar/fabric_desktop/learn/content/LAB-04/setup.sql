-- silver_orders after two nightly loads that only appended. Day 2's job failed after writing and was
-- retried, so day 2's rows are in it twice; order 3 changed on day 2, so it has an old row too.
CREATE TABLE silver_orders AS
SELECT order_id, status, CAST(amount AS DECIMAL(12,2)) AS amount, loaded_at FROM (VALUES
    (1, 'open',     10.00, TIMESTAMP '2026-03-02 01:00:00'),
    (2, 'complete', 20.00, TIMESTAMP '2026-03-02 01:00:00'),
    (3, 'open',     30.00, TIMESTAMP '2026-03-02 01:00:00'),
    (3, 'complete', 30.00, TIMESTAMP '2026-03-03 01:00:00'),
    (4, 'open',     40.00, TIMESTAMP '2026-03-03 01:00:00'),
    (3, 'complete', 30.00, TIMESTAMP '2026-03-03 01:20:00'),
    (4, 'open',     40.00, TIMESTAMP '2026-03-03 01:20:00')
) AS t(order_id, status, amount, loaded_at);

-- The same orders, cleaned, and day 3's changes: order 4 completed, order 5 is new.
CREATE TABLE silver_clean AS
SELECT order_id, status, CAST(amount AS DECIMAL(12,2)) AS amount, loaded_at FROM (VALUES
    (1, 'open',     10.00, TIMESTAMP '2026-03-02 01:00:00'),
    (2, 'complete', 20.00, TIMESTAMP '2026-03-02 01:00:00'),
    (3, 'complete', 30.00, TIMESTAMP '2026-03-03 01:20:00'),
    (4, 'open',     40.00, TIMESTAMP '2026-03-03 01:20:00')
) AS t(order_id, status, amount, loaded_at);

CREATE TABLE src_orders_day3 AS
SELECT order_id, status, CAST(amount AS DECIMAL(12,2)) AS amount, loaded_at FROM (VALUES
    (4, 'complete', 40.00, TIMESTAMP '2026-03-04 01:00:00'),
    (5, 'open',     55.00, TIMESTAMP '2026-03-04 01:00:00')
) AS t(order_id, status, amount, loaded_at);

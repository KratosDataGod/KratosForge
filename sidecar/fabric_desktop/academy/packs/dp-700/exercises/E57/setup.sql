CREATE TABLE source_orders AS
SELECT * FROM (VALUES
    (1001, DATE '2026-04-01', 40.00::DECIMAL(12,2), TIMESTAMP '2026-04-01 09:00:00'),
    (1002, DATE '2026-04-01', 15.50::DECIMAL(12,2), TIMESTAMP '2026-04-01 10:30:00'),
    (1003, DATE '2026-04-02', 99.00::DECIMAL(12,2), TIMESTAMP '2026-04-02 08:15:00'),
    (1004, DATE '2026-04-02', 12.25::DECIMAL(12,2), TIMESTAMP '2026-04-02 11:00:00'),
    (1005, DATE '2026-04-03', 70.00::DECIMAL(12,2), TIMESTAMP '2026-04-03 09:45:00'),
    (1006, DATE '2026-04-01', 25.00::DECIMAL(12,2), TIMESTAMP '2026-04-03 10:05:00')
) AS t(order_id, order_date, amount, arrived_at);

-- Loaded by earlier runs: 1001 to 1004.
CREATE TABLE silver_orders AS SELECT * FROM source_orders WHERE order_id <= 1004;

-- The last run stored 2 April as its watermark.
CREATE TABLE load_state (last_watermark TIMESTAMP);
INSERT INTO load_state VALUES (TIMESTAMP '2026-04-02 00:00:00');

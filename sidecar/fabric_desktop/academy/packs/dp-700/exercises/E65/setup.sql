-- Tonight's staged orders, as text exactly as the source sent them. An order can be sent again with a later version_ts.
CREATE TABLE stg_orders (order_id INTEGER, version_ts TIMESTAMP, qty_text VARCHAR, amount_text VARCHAR, cost_text VARCHAR);
INSERT INTO stg_orders VALUES
    (1,  TIMESTAMP '2026-03-01 08:00:00', '2',   '20.00', '12.00'),
    (2,  TIMESTAMP '2026-03-01 08:00:00', '1',   'N/A',   '4.00'),
    (3,  TIMESTAMP '2026-03-01 08:00:00', '3',   '30.00', '18.00'),
    (3,  TIMESTAMP '2026-03-01 09:30:00', '3',   '33.00', '18.00'),
    (4,  TIMESTAMP '2026-03-01 08:00:00', '1',   '0.00',  '0.00'),
    (9,  TIMESTAMP '2026-03-01 08:00:00', '1',   '15.00', '9.00'),
    (9,  TIMESTAMP '2026-03-01 10:00:00', '2',   '30.00', '18.00'),
    (10, TIMESTAMP '2026-03-01 08:00:00', 'two', '10.00', '6.00');

-- The warehouse fact table before tonight's load.
CREATE TABLE fact_orders (order_id INTEGER, qty INTEGER, amount DECIMAL(12,2), cost DECIMAL(12,2), margin_pct DECIMAL(9,4));
INSERT INTO fact_orders VALUES
    (1, 1, 10.00, 6.00, 0.4000),
    (3, 3, 29.00, 18.00, 0.3793),
    (5, 1, 8.00, 5.00, 0.3750);

-- Where rows that can't be loaded go, with the reason.
CREATE TABLE order_rejects (order_id INTEGER, reason VARCHAR);

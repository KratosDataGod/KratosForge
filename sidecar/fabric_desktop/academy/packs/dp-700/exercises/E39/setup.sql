-- Detail kept for recent days only.
CREATE TABLE events (event_id VARCHAR, event_date DATE, amount DECIMAL(12,2));
INSERT INTO events VALUES
    ('e1', DATE '2026-05-03', 10.00), ('e2', DATE '2026-05-03', 20.00),
    ('e3', DATE '2026-05-04', 20.00), ('e4', DATE '2026-05-05', 10.00);

-- The dashboard's table. 1 and 2 May come from events that have been archived.
CREATE TABLE daily_sales (sale_date DATE, total DECIMAL(38,2));
INSERT INTO daily_sales VALUES
    (DATE '2026-05-01', 100.00), (DATE '2026-05-02', 80.00), (DATE '2026-05-03', 30.00),
    (DATE '2026-05-04', 20.00), (DATE '2026-05-05', 10.00);

-- Today's upload: e3 again (a retry), two late events and one for today.
CREATE TABLE stg_events (event_id VARCHAR, event_date DATE, amount DECIMAL(12,2));
INSERT INTO stg_events VALUES
    ('e3', DATE '2026-05-04', 20.00), ('e6', DATE '2026-05-04', 5.00),
    ('e7', DATE '2026-05-05', 7.00), ('e8', DATE '2026-05-06', 9.00);

CREATE TABLE store_stats (region VARCHAR, store VARCHAR, orders INTEGER, returns INTEGER, discount DECIMAL(5, 2));
INSERT INTO store_stats VALUES
    ('North', 'N1', 100, 5, NULL),
    ('North', 'N2', 0, 0, 2.50),
    ('North', 'N3', 50, 10, NULL),
    ('South', 'S1', 80, 4, 1.00),
    ('South', 'S2', 40, 8, NULL);

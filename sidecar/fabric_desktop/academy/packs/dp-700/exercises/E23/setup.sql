-- Staging copy of the CRM's customers. Customer 102 was loaded twice; regions are typed three ways;
-- customer 104 has no region.
CREATE TABLE stg_customers AS
SELECT * FROM (VALUES
    (101, 'Ana Ruiz',   'north', TIMESTAMP '2026-05-01 02:00:00'),
    (102, 'Ben Okafor', 'South', TIMESTAMP '2026-05-01 02:00:00'),
    (103, 'Chen Wei',   'EAST',  TIMESTAMP '2026-05-01 02:00:00'),
    (102, 'Ben Okafor', 'South', TIMESTAMP '2026-05-02 02:00:00'),
    (104, 'Dee Patel',  NULL,    TIMESTAMP '2026-05-02 02:00:00')
) AS t(customer_id, name, region, loaded_at);

-- The agreed region names.
CREATE TABLE ref_region AS
SELECT * FROM (VALUES ('north', 'North'), ('south', 'South'), ('east', 'East'), ('west', 'West'))
AS t(region_code, region_name);

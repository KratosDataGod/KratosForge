-- E26 practice data: silver.customers as it stands, and today's change batch in bronze.customer_changes.
-- A customer can change more than once in a batch; op is I (insert), U (update) or D (delete).
-- DuckDB SQL; deterministic, nothing random.
CREATE SCHEMA bronze;
CREATE SCHEMA silver;
CREATE TABLE silver.customers (customer_id BIGINT, name VARCHAR, city VARCHAR, updated_at TIMESTAMP);
INSERT INTO silver.customers VALUES
  (1, 'Ana',   'Lisbon',  TIMESTAMP '2026-03-01 09:00:00'),
  (2, 'Ben',   'Leeds',   TIMESTAMP '2026-03-01 09:00:00'),
  (3, 'Chloe', 'Lyon',    TIMESTAMP '2026-03-01 09:00:00'),
  (4, 'Dev',   'Pune',    TIMESTAMP '2026-03-01 09:00:00');
CREATE TABLE bronze.customer_changes (customer_id BIGINT, op VARCHAR, name VARCHAR, city VARCHAR, change_ts TIMESTAMP);
INSERT INTO bronze.customer_changes VALUES
  (2, 'U', 'Ben',   'York',    TIMESTAMP '2026-03-02 08:00:00'),
  (2, 'U', 'Ben',   'Hull',    TIMESTAMP '2026-03-02 10:00:00'),
  (3, 'D', NULL,    NULL,      TIMESTAMP '2026-03-02 09:00:00'),
  (5, 'I', 'Eva',   'Oslo',    TIMESTAMP '2026-03-02 09:30:00'),
  (6, 'I', 'Femi',  'Lagos',   TIMESTAMP '2026-03-02 11:00:00'),
  (6, 'D', NULL,    NULL,      TIMESTAMP '2026-03-02 12:00:00'),
  (7, 'D', NULL,    NULL,      TIMESTAMP '2026-03-02 12:30:00');

-- E26 grading data the learner never sees: other keys, a delete then a re-insert of the same customer,
-- three updates in one batch, and a delete for a customer silver never had. DuckDB SQL.
CREATE SCHEMA bronze;
CREATE SCHEMA silver;
CREATE TABLE silver.customers (customer_id BIGINT, name VARCHAR, city VARCHAR, updated_at TIMESTAMP);
INSERT INTO silver.customers VALUES
  (10, 'Kai',  'Kyoto',  TIMESTAMP '2026-04-01 09:00:00'),
  (11, 'Lea',  'Lille',  TIMESTAMP '2026-04-01 09:00:00'),
  (12, 'Moe',  'Malmo',  TIMESTAMP '2026-04-01 09:00:00');
CREATE TABLE bronze.customer_changes (customer_id BIGINT, op VARCHAR, name VARCHAR, city VARCHAR, change_ts TIMESTAMP);
INSERT INTO bronze.customer_changes VALUES
  (10, 'D', NULL,   NULL,     TIMESTAMP '2026-04-02 08:00:00'),
  (10, 'I', 'Kai',  'Osaka',  TIMESTAMP '2026-04-02 09:00:00'),
  (11, 'U', 'Lea',  'Paris',  TIMESTAMP '2026-04-02 08:00:00'),
  (11, 'U', 'Lea',  'Nice',   TIMESTAMP '2026-04-02 09:00:00'),
  (11, 'U', 'Léa',  'Nice',   TIMESTAMP '2026-04-02 10:00:00'),
  (13, 'I', 'Nia',  'Nairobi',TIMESTAMP '2026-04-02 11:00:00'),
  (14, 'D', NULL,   NULL,     TIMESTAMP '2026-04-02 12:00:00');

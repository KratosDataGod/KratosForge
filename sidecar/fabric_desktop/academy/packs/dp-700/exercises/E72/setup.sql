-- A made-up nightly ingestion, the same every time: the Copy activities the pipeline should run each night, and
-- the activity runs it recorded, shaped like a Copy activity's run output. activity_run_id is an opaque id, as it is
-- in Fabric, so it says nothing about order. rows_skipped is only reported by an activity with fault tolerance
-- (skip incompatible rows) turned on; it's NULL for the others.
CREATE TABLE copy_activities AS SELECT * FROM (VALUES
  ('Copy customers', 'crm.Customer', 'bronze.customers'),
  ('Copy orders', 'sales.Orders', 'bronze.orders'),
  ('Copy products', 'erp.Product', 'bronze.products'),
  ('Copy promotions', 'mkt.Promotion', 'bronze.promotions'),
  ('Copy returns', 'sales.Returns', 'bronze.returns'),
  ('Copy stores', 'erp.Store', 'bronze.stores'),
  ('Copy suppliers', 'erp.Supplier', 'bronze.suppliers')
) AS t(activity_name, source_table, destination_table);

CREATE TABLE copy_runs AS
SELECT md5(activity_name || CAST(run_start AS VARCHAR) || 'nightly') AS activity_run_id, 'Nightly ingest' AS pipeline_name,
       activity_name, run_start, run_start + to_seconds(copy_duration) AS run_end, status,
       rows_read, rows_copied, rows_skipped, copy_duration, error_message
FROM (VALUES
  ('Copy customers', TIMESTAMP '2026-11-14 02:00:05', 'Succeeded', 11950, 11950, 0, 41, NULL),
  ('Copy customers', TIMESTAMP '2026-11-15 02:00:04', 'Succeeded', 11968, 11968, 0, 43, NULL),
  ('Copy customers', TIMESTAMP '2026-11-16 02:00:06', 'Succeeded', 11990, 11990, 0, 40, NULL),
  ('Copy customers', TIMESTAMP '2026-11-17 02:00:05', 'Succeeded', 12037, 12000, 37, 44, NULL),
  ('Copy orders', TIMESTAMP '2026-11-14 02:01:10', 'Succeeded', 47120, 47120, NULL, 95, NULL),
  ('Copy orders', TIMESTAMP '2026-11-15 02:01:12', 'Succeeded', 47480, 47480, NULL, 97, NULL),
  ('Copy orders', TIMESTAMP '2026-11-16 02:01:09', 'Succeeded', 47900, 47900, NULL, 99, NULL),
  ('Copy orders', TIMESTAMP '2026-11-17 02:01:11', 'Failed', 0, 0, NULL, 60, 'ErrorCode=SqlFailedToConnect: Cannot connect to the source database. The connection timed out after 60 seconds.'),
  ('Copy orders', TIMESTAMP '2026-11-17 06:12:40', 'Succeeded', 48210, 48210, NULL, 101, NULL),
  ('Copy products', TIMESTAMP '2026-11-14 02:00:20', 'Succeeded', 3410, 3410, NULL, 12, NULL),
  ('Copy products', TIMESTAMP '2026-11-15 02:00:21', 'Succeeded', 3412, 3412, NULL, 12, NULL),
  ('Copy products', TIMESTAMP '2026-11-16 02:00:19', 'Succeeded', 3415, 3415, NULL, 13, NULL),
  ('Copy products', TIMESTAMP '2026-11-17 02:00:20', 'Succeeded', 0, 0, NULL, 6, NULL),
  ('Copy returns', TIMESTAMP '2026-11-14 02:02:30', 'Succeeded', 820, 820, NULL, 9, NULL),
  ('Copy returns', TIMESTAMP '2026-11-15 02:02:31', 'Succeeded', 835, 835, NULL, 9, NULL),
  ('Copy returns', TIMESTAMP '2026-11-16 02:02:29', 'Succeeded', 841, 841, NULL, 10, NULL),
  ('Copy returns', TIMESTAMP '2026-11-17 02:02:30', 'Failed', 0, 0, NULL, 4, 'ErrorCode=UserErrorInvalidColumnName: Invalid column name ''reason_code'' in the source query.'),
  ('Copy stores', TIMESTAMP '2026-11-12 02:03:00', 'Succeeded', 212, 212, NULL, 5, NULL),
  ('Copy stores', TIMESTAMP '2026-11-13 02:03:01', 'Succeeded', 212, 212, NULL, 5, NULL),
  ('Copy stores', TIMESTAMP '2026-11-14 02:03:00', 'Succeeded', 213, 213, NULL, 5, NULL),
  ('Copy suppliers', TIMESTAMP '2026-11-14 02:03:40', 'Succeeded', 1290, 1290, 0, 7, NULL),
  ('Copy suppliers', TIMESTAMP '2026-11-15 02:03:41', 'Succeeded', 1291, 1291, 0, 7, NULL),
  ('Copy suppliers', TIMESTAMP '2026-11-16 02:03:39', 'Succeeded', 1291, 1291, 0, 7, NULL),
  ('Copy suppliers', TIMESTAMP '2026-11-17 02:03:40', 'Succeeded', 1294, 1294, 0, 8, NULL)
) AS t(activity_name, run_start, status, rows_read, rows_copied, rows_skipped, copy_duration, error_message)
ORDER BY activity_run_id;

-- When the morning check runs. A nightly activity whose latest run started more than 24 hours before this didn't run.
CREATE TABLE ingestion_check AS SELECT TIMESTAMP '2026-11-17 07:30:00' AS checked_at;

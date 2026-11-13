-- A made-up tenant, the same every time: five Direct Lake semantic models on an F64 capacity, their
-- refresh history, their tables, and a log of report queries. Ids are opaque, as they are in Fabric.
CREATE TABLE models AS SELECT * FROM (VALUES
  ('Finance model', 'Automatic'),
  ('HR model', 'Automatic'),
  ('Ops model', 'DirectQueryOnly'),
  ('Sales model', 'Automatic'),
  ('Stock model', 'Automatic')
) AS t(model_name, direct_lake_behavior);

-- One row per refresh. A Direct Lake refresh is framing: it points the model at the latest Delta version.
CREATE TABLE refresh_history AS
SELECT md5(model_name || CAST(start_time AS VARCHAR)) AS refresh_id, model_name, 'Scheduled' AS refresh_type,
       start_time, end_time, status, error_message
FROM (VALUES
  ('Finance model', TIMESTAMP '2026-10-20 06:05:00', TIMESTAMP '2026-10-20 06:05:00' + INTERVAL 14 SECOND, 'Completed', NULL),
  ('Finance model', TIMESTAMP '2026-10-21 06:05:00', TIMESTAMP '2026-10-21 06:05:00' + INTERVAL 15 SECOND, 'Completed', NULL),
  ('Finance model', TIMESTAMP '2026-10-22 06:05:00', TIMESTAMP '2026-10-22 06:05:00' + INTERVAL 16 SECOND, 'Completed', NULL),
  ('Finance model', TIMESTAMP '2026-10-23 06:05:00', TIMESTAMP '2026-10-23 06:05:00' + INTERVAL 17 SECOND, 'Completed', NULL),
  ('Finance model', TIMESTAMP '2026-10-24 06:05:00', TIMESTAMP '2026-10-24 06:05:00' + INTERVAL 18 SECOND, 'Completed', NULL),
  ('Finance model', TIMESTAMP '2026-10-25 06:05:00', TIMESTAMP '2026-10-25 06:05:00' + INTERVAL 19 SECOND, 'Failed', 'Framing failed: column ''Amount'' in table ''gl_entries'' wasn''t found in the Delta table.'),
  ('HR model', TIMESTAMP '2026-10-20 06:08:00', TIMESTAMP '2026-10-20 06:08:00' + INTERVAL 17 SECOND, 'Completed', NULL),
  ('HR model', TIMESTAMP '2026-10-21 06:08:00', TIMESTAMP '2026-10-21 06:08:00' + INTERVAL 18 SECOND, 'Completed', NULL),
  ('HR model', TIMESTAMP '2026-10-22 06:08:00', TIMESTAMP '2026-10-22 06:08:00' + INTERVAL 19 SECOND, 'Completed', NULL),
  ('HR model', TIMESTAMP '2026-10-23 06:08:00', TIMESTAMP '2026-10-23 06:08:00' + INTERVAL 20 SECOND, 'Completed', NULL),
  ('HR model', TIMESTAMP '2026-10-24 06:08:00', TIMESTAMP '2026-10-24 06:08:00' + INTERVAL 21 SECOND, 'Completed', NULL),
  ('HR model', TIMESTAMP '2026-10-25 06:08:00', TIMESTAMP '2026-10-25 06:08:00' + INTERVAL 22 SECOND, 'Completed', NULL),
  ('Ops model', TIMESTAMP '2026-10-20 06:11:00', TIMESTAMP '2026-10-20 06:11:00' + INTERVAL 20 SECOND, 'Completed', NULL),
  ('Ops model', TIMESTAMP '2026-10-21 06:11:00', TIMESTAMP '2026-10-21 06:11:00' + INTERVAL 21 SECOND, 'Completed', NULL),
  ('Ops model', TIMESTAMP '2026-10-22 06:11:00', TIMESTAMP '2026-10-22 06:11:00' + INTERVAL 22 SECOND, 'Completed', NULL),
  ('Ops model', TIMESTAMP '2026-10-23 06:11:00', TIMESTAMP '2026-10-23 06:11:00' + INTERVAL 23 SECOND, 'Completed', NULL),
  ('Ops model', TIMESTAMP '2026-10-24 06:11:00', TIMESTAMP '2026-10-24 06:11:00' + INTERVAL 24 SECOND, 'Completed', NULL),
  ('Ops model', TIMESTAMP '2026-10-25 06:11:00', TIMESTAMP '2026-10-25 06:11:00' + INTERVAL 25 SECOND, 'Completed', NULL),
  ('Sales model', TIMESTAMP '2026-10-20 06:14:00', TIMESTAMP '2026-10-20 06:14:00' + INTERVAL 23 SECOND, 'Completed', NULL),
  ('Sales model', TIMESTAMP '2026-10-21 06:14:00', TIMESTAMP '2026-10-21 06:14:00' + INTERVAL 24 SECOND, 'Completed', NULL),
  ('Sales model', TIMESTAMP '2026-10-22 06:14:00', TIMESTAMP '2026-10-22 06:14:00' + INTERVAL 25 SECOND, 'Completed', NULL),
  ('Sales model', TIMESTAMP '2026-10-23 06:14:00', TIMESTAMP '2026-10-23 06:14:00' + INTERVAL 900 SECOND, 'Failed', 'The refresh was canceled because it exceeded the time limit.'),
  ('Sales model', TIMESTAMP '2026-10-24 06:14:00', TIMESTAMP '2026-10-24 06:14:00' + INTERVAL 27 SECOND, 'Completed', NULL),
  ('Sales model', TIMESTAMP '2026-10-25 06:14:00', TIMESTAMP '2026-10-25 06:14:00' + INTERVAL 28 SECOND, 'Completed', NULL),
  ('Stock model', TIMESTAMP '2026-10-20 06:17:00', TIMESTAMP '2026-10-20 06:17:00' + INTERVAL 26 SECOND, 'Completed', NULL),
  ('Stock model', TIMESTAMP '2026-10-21 06:17:00', TIMESTAMP '2026-10-21 06:17:00' + INTERVAL 27 SECOND, 'Completed', NULL),
  ('Stock model', TIMESTAMP '2026-10-22 06:17:00', TIMESTAMP '2026-10-22 06:17:00' + INTERVAL 28 SECOND, 'Completed', NULL),
  ('Stock model', TIMESTAMP '2026-10-23 06:17:00', TIMESTAMP '2026-10-23 06:17:00' + INTERVAL 29 SECOND, 'Completed', NULL),
  ('Stock model', TIMESTAMP '2026-10-24 06:17:00', TIMESTAMP '2026-10-24 06:17:00' + INTERVAL 30 SECOND, 'Completed', NULL),
  ('Stock model', TIMESTAMP '2026-10-25 06:17:00', TIMESTAMP '2026-10-25 06:17:00' + INTERVAL 31 SECOND, 'Completed', NULL)
) AS t(model_name, start_time, end_time, status, error_message)
ORDER BY refresh_id;

-- The tables each model reads through the SQL analytics endpoint, with their Delta sizes. rows_millions is in millions.
CREATE TABLE model_tables AS SELECT * FROM (VALUES
  ('Finance model', 'accounts', 'table', 4, 4, 0.01),
  ('Finance model', 'gl_entries', 'table', 820, 910, 310),
  ('HR model', 'employees', 'table', 12, 12, 0.05),
  ('HR model', 'vw_headcount', 'view', NULL, NULL, NULL),
  ('Ops model', 'shipments', 'table', 640, 700, 95),
  ('Sales model', 'customers', 'table', 40, 40, 2),
  ('Sales model', 'sales', 'table', 5000, 4800, 1450),
  ('Stock model', 'inventory_snapshot', 'table', 4200, 6100, 980),
  ('Stock model', 'products', 'table', 10, 10, 0.2),
  ('Stock model', 'stock_levels', 'table', 300, 320, 45)
) AS t(model_name, table_name, source_kind, parquet_files, row_groups, rows_millions);

-- Report queries, with the mode each one actually ran in.
CREATE TABLE query_log AS
SELECT md5(model_name || CAST(query_time AS VARCHAR)) AS query_id, model_name, query_time, table_name, mode
FROM (VALUES
  ('Finance model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 0 MINUTE, 'gl_entries', 'DirectLake'),
  ('Finance model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 7 MINUTE, 'gl_entries', 'DirectLake'),
  ('Finance model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 14 MINUTE, 'gl_entries', 'DirectLake'),
  ('Finance model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 21 MINUTE, 'accounts', 'DirectLake'),
  ('HR model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 28 MINUTE, 'employees', 'DirectLake'),
  ('HR model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 35 MINUTE, 'employees', 'DirectLake'),
  ('HR model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 42 MINUTE, 'employees', 'DirectLake'),
  ('HR model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 49 MINUTE, 'vw_headcount', 'DirectQuery'),
  ('HR model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 56 MINUTE, 'vw_headcount', 'DirectQuery'),
  ('Ops model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 63 MINUTE, 'shipments', 'DirectQuery'),
  ('Ops model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 70 MINUTE, 'shipments', 'DirectQuery'),
  ('Ops model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 77 MINUTE, 'shipments', 'DirectQuery'),
  ('Ops model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 84 MINUTE, 'shipments', 'DirectQuery'),
  ('Sales model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 91 MINUTE, 'sales', 'DirectLake'),
  ('Sales model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 98 MINUTE, 'sales', 'DirectLake'),
  ('Sales model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 105 MINUTE, 'sales', 'DirectLake'),
  ('Sales model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 112 MINUTE, 'sales', 'DirectLake'),
  ('Sales model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 119 MINUTE, 'customers', 'DirectLake'),
  ('Sales model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 126 MINUTE, 'customers', 'DirectLake'),
  ('Stock model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 133 MINUTE, 'stock_levels', 'DirectQuery'),
  ('Stock model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 140 MINUTE, 'stock_levels', 'DirectQuery'),
  ('Stock model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 147 MINUTE, 'stock_levels', 'DirectQuery'),
  ('Stock model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 154 MINUTE, 'products', 'DirectQuery'),
  ('Stock model', TIMESTAMP '2026-10-25 09:00:00' + INTERVAL 161 MINUTE, 'products', 'DirectQuery')
) AS t(model_name, query_time, table_name, mode)
ORDER BY query_id;

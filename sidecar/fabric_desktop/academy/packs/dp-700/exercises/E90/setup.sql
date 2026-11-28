-- A made-up tenant, the same every time: five semantic models on one capacity, each refreshed by its
-- schedule at about 02:00, and eight nights of refresh history. Ids are opaque, as they are in Fabric.
CREATE TABLE models AS SELECT * FROM (VALUES
  ('Finance model', 'Import'),
  ('HR model', 'Import'),
  ('Ops model', 'Import'),
  ('Sales model', 'Import'),
  ('Stock model', 'Direct Lake')
) AS t(model_name, storage_mode);

-- One row per refresh, shaped like the refresh history page. error_type sorts the error message into
-- DataSource, Credentials, Timeout or Framing. A Direct Lake refresh is framing, so it takes seconds.
CREATE TABLE refresh_history AS
SELECT md5(model_name || CAST(start_time AS VARCHAR)) AS refresh_id, model_name, 'Scheduled' AS refresh_type,
       start_time, end_time, status, error_type, error_message
FROM (VALUES
  ('Finance model', TIMESTAMP '2026-11-14 02:05:00', TIMESTAMP '2026-11-14 02:05:00' + INTERVAL 20 MINUTE, 'Completed', NULL, NULL),
  ('Finance model', TIMESTAMP '2026-11-15 02:05:00', TIMESTAMP '2026-11-15 02:05:00' + INTERVAL 21 MINUTE, 'Completed', NULL, NULL),
  ('Finance model', TIMESTAMP '2026-11-16 02:05:00', TIMESTAMP '2026-11-16 02:05:00' + INTERVAL 5 MINUTE, 'Failed', 'DataSource', 'The data source didn''t respond before the refresh timed out.'),
  ('Finance model', TIMESTAMP '2026-11-17 02:05:00', TIMESTAMP '2026-11-17 02:05:00' + INTERVAL 22 MINUTE, 'Completed', NULL, NULL),
  ('Finance model', TIMESTAMP '2026-11-18 02:05:00', TIMESTAMP '2026-11-18 02:05:00' + INTERVAL 3 MINUTE, 'Failed', 'DataSource', 'The data source didn''t respond before the refresh timed out.'),
  ('Finance model', TIMESTAMP '2026-11-19 02:05:00', TIMESTAMP '2026-11-19 02:05:00' + INTERVAL 3 MINUTE, 'Failed', 'DataSource', 'The data source didn''t respond before the refresh timed out.'),
  ('Finance model', TIMESTAMP '2026-11-20 02:05:00', TIMESTAMP '2026-11-20 02:05:00' + INTERVAL 3 MINUTE, 'Failed', 'DataSource', 'The data source didn''t respond before the refresh timed out.'),
  ('Finance model', TIMESTAMP '2026-11-21 02:05:00', TIMESTAMP '2026-11-21 02:05:00' + INTERVAL 3 MINUTE, 'Failed', 'DataSource', 'The data source didn''t respond before the refresh timed out.'),
  ('HR model', TIMESTAMP '2026-11-14 02:10:00', TIMESTAMP '2026-11-14 02:10:00' + INTERVAL 10 MINUTE, 'Completed', NULL, NULL),
  ('HR model', TIMESTAMP '2026-11-15 02:10:00', TIMESTAMP '2026-11-15 02:10:00' + INTERVAL 11 MINUTE, 'Completed', NULL, NULL),
  ('HR model', TIMESTAMP '2026-11-16 02:10:00', TIMESTAMP '2026-11-16 02:10:00' + INTERVAL 10 MINUTE, 'Completed', NULL, NULL),
  ('HR model', TIMESTAMP '2026-11-17 02:10:00', TIMESTAMP '2026-11-17 02:10:00' + INTERVAL 12 MINUTE, 'Completed', NULL, NULL),
  ('HR model', TIMESTAMP '2026-11-18 02:10:00', TIMESTAMP '2026-11-18 02:10:00' + INTERVAL 11 MINUTE, 'Completed', NULL, NULL),
  ('HR model', TIMESTAMP '2026-11-19 02:10:00', TIMESTAMP '2026-11-19 02:10:00' + INTERVAL 2 MINUTE, 'Failed', 'DataSource', 'The data source didn''t respond before the refresh timed out.'),
  ('HR model', TIMESTAMP '2026-11-20 02:10:00', TIMESTAMP '2026-11-20 02:10:00' + INTERVAL 2 MINUTE, 'Failed', 'DataSource', 'The data source didn''t respond before the refresh timed out.'),
  ('HR model', TIMESTAMP '2026-11-21 02:10:00', TIMESTAMP '2026-11-21 02:10:00' + INTERVAL 2 MINUTE, 'Failed', 'DataSource', 'The data source didn''t respond before the refresh timed out.'),
  ('Ops model', TIMESTAMP '2026-11-14 02:15:00', TIMESTAMP '2026-11-14 02:15:00' + INTERVAL 30 MINUTE, 'Completed', NULL, NULL),
  ('Ops model', TIMESTAMP '2026-11-15 02:15:00', TIMESTAMP '2026-11-15 02:15:00' + INTERVAL 31 MINUTE, 'Completed', NULL, NULL),
  ('Ops model', TIMESTAMP '2026-11-16 02:15:00', TIMESTAMP '2026-11-16 02:15:00' + INTERVAL 29 MINUTE, 'Completed', NULL, NULL),
  ('Ops model', TIMESTAMP '2026-11-17 02:15:00', TIMESTAMP '2026-11-17 02:15:00' + INTERVAL 95 MINUTE, 'Completed', NULL, NULL),
  ('Ops model', TIMESTAMP '2026-11-18 02:15:00', TIMESTAMP '2026-11-18 02:15:00' + INTERVAL 30 MINUTE, 'Completed', NULL, NULL),
  ('Ops model', TIMESTAMP '2026-11-19 02:15:00', TIMESTAMP '2026-11-19 02:15:00' + INTERVAL 32 MINUTE, 'Completed', NULL, NULL),
  ('Ops model', TIMESTAMP '2026-11-20 02:15:00', TIMESTAMP '2026-11-20 02:15:00' + INTERVAL 30 MINUTE, 'Completed', NULL, NULL),
  ('Ops model', TIMESTAMP '2026-11-21 02:15:00', TIMESTAMP '2026-11-21 02:15:00' + INTERVAL 1 MINUTE, 'Failed', 'Credentials', 'The credentials for the data source are invalid or have expired.'),
  ('Sales model', TIMESTAMP '2026-11-14 02:20:00', TIMESTAMP '2026-11-14 02:20:00' + INTERVAL 38 MINUTE, 'Completed', NULL, NULL),
  ('Sales model', TIMESTAMP '2026-11-15 02:20:00', TIMESTAMP '2026-11-15 02:20:00' + INTERVAL 80 MINUTE, 'Completed', NULL, NULL),
  ('Sales model', TIMESTAMP '2026-11-16 02:20:00', TIMESTAMP '2026-11-16 02:20:00' + INTERVAL 41 MINUTE, 'Completed', NULL, NULL),
  ('Sales model', TIMESTAMP '2026-11-17 02:20:00', TIMESTAMP '2026-11-17 02:20:00' + INTERVAL 39 MINUTE, 'Completed', NULL, NULL),
  ('Sales model', TIMESTAMP '2026-11-18 02:20:00', TIMESTAMP '2026-11-18 02:20:00' + INTERVAL 40 MINUTE, 'Completed', NULL, NULL),
  ('Sales model', TIMESTAMP '2026-11-19 02:20:00', TIMESTAMP '2026-11-19 02:20:00' + INTERVAL 300 MINUTE, 'Failed', 'Timeout', 'The refresh was canceled because it exceeded the time limit.'),
  ('Sales model', TIMESTAMP '2026-11-20 02:20:00', TIMESTAMP '2026-11-20 02:20:00' + INTERVAL 42 MINUTE, 'Completed', NULL, NULL),
  ('Sales model', TIMESTAMP '2026-11-21 02:20:00', TIMESTAMP '2026-11-21 02:20:00' + INTERVAL 40 MINUTE, 'Completed', NULL, NULL),
  ('Stock model', TIMESTAMP '2026-11-14 02:25:00', TIMESTAMP '2026-11-14 02:25:00' + INTERVAL 18 SECOND, 'Completed', NULL, NULL),
  ('Stock model', TIMESTAMP '2026-11-15 02:25:00', TIMESTAMP '2026-11-15 02:25:00' + INTERVAL 20 SECOND, 'Completed', NULL, NULL),
  ('Stock model', TIMESTAMP '2026-11-16 02:25:00', TIMESTAMP '2026-11-16 02:25:00' + INTERVAL 9 SECOND, 'Failed', 'Framing', 'Framing failed: a Delta table is over the capacity guardrails.'),
  ('Stock model', TIMESTAMP '2026-11-17 02:25:00', TIMESTAMP '2026-11-17 02:25:00' + INTERVAL 22 SECOND, 'Completed', NULL, NULL),
  ('Stock model', TIMESTAMP '2026-11-18 02:25:00', TIMESTAMP '2026-11-18 02:25:00' + INTERVAL 70 SECOND, 'Completed', NULL, NULL),
  ('Stock model', TIMESTAMP '2026-11-19 02:25:00', TIMESTAMP '2026-11-19 02:25:00' + INTERVAL 19 SECOND, 'Completed', NULL, NULL),
  ('Stock model', TIMESTAMP '2026-11-20 02:25:00', TIMESTAMP '2026-11-20 02:25:00' + INTERVAL 21 SECOND, 'Completed', NULL, NULL),
  ('Stock model', TIMESTAMP '2026-11-21 02:25:00', TIMESTAMP '2026-11-21 02:25:00' + INTERVAL 20 SECOND, 'Completed', NULL, NULL)
) AS t(model_name, start_time, end_time, status, error_type, error_message)
ORDER BY refresh_id;

-- C2 practice data: three made-up chillers, one run's worth of new readings. DuckDB SQL; fixed values.
CREATE SCHEMA bronze;
CREATE SCHEMA silver;
CREATE SCHEMA gold;
-- Readings the chillers sent since the last run, as the eventstream appended them. One event can arrive twice.
CREATE TABLE bronze.device_events AS SELECT * FROM (VALUES
  ('e04', 'c2', TIMESTAMP '2026-10-20 09:54:00', 9.4::DECIMAL(5,1)),
  ('e05', 'c1', TIMESTAMP '2026-10-20 09:51:30', 14.0::DECIMAL(5,1)),
  ('e06', 'c1', TIMESTAMP '2026-10-20 10:01:00', 6.5::DECIMAL(5,1)),
  ('e07', 'c1', TIMESTAMP '2026-10-20 10:03:00', 6.0::DECIMAL(5,1)),
  ('e07', 'c1', TIMESTAMP '2026-10-20 10:03:00', 6.0::DECIMAL(5,1)),
  ('e08', 'c2', TIMESTAMP '2026-10-20 10:00:00', 7.5::DECIMAL(5,1)),
  ('e09', 'c2', TIMESTAMP '2026-10-20 10:04:00', 7.9::DECIMAL(5,1)),
  ('e10', 'c3', TIMESTAMP '2026-10-20 10:02:00', 3.0::DECIMAL(5,1))
) AS t(event_id, device_id, event_time, temperature);
-- What the last run left: one row per event, the 5-minute windows, and the windows over the limit.
CREATE TABLE silver.readings AS SELECT * FROM (VALUES
  ('e01', 'c1', TIMESTAMP '2026-10-20 09:51:00', 5.0::DECIMAL(5,1)),
  ('e02', 'c1', TIMESTAMP '2026-10-20 09:53:00', 6.0::DECIMAL(5,1)),
  ('e03', 'c2', TIMESTAMP '2026-10-20 09:52:00', 9.0::DECIMAL(5,1)),
  ('e04', 'c2', TIMESTAMP '2026-10-20 09:54:00', 9.4::DECIMAL(5,1))
) AS t(event_id, device_id, event_time, temperature);
CREATE TABLE gold.device_5min AS SELECT * FROM (VALUES
  ('c1', TIMESTAMP '2026-10-20 09:50:00', 2::BIGINT, 5.50::DECIMAL(6,2), 6.0::DECIMAL(5,1)),
  ('c2', TIMESTAMP '2026-10-20 09:50:00', 2::BIGINT, 9.20::DECIMAL(6,2), 9.4::DECIMAL(5,1))
) AS t(device_id, window_start, readings, avg_temp, max_temp);
CREATE TABLE gold.alerts AS SELECT * FROM (VALUES
  ('c2', TIMESTAMP '2026-10-20 09:50:00', 9.20::DECIMAL(6,2))
) AS t(device_id, window_start, avg_temp);

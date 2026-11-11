-- C2 grading data the learner never sees: other chillers, a late reading that clears an alert. DuckDB SQL.
CREATE SCHEMA bronze;
CREATE SCHEMA silver;
CREATE SCHEMA gold;
-- Readings the chillers sent since the last run, as the eventstream appended them. One event can arrive twice.
CREATE TABLE bronze.device_events AS SELECT * FROM (VALUES
  ('h05', 'k1', TIMESTAMP '2026-10-21 14:01:00', 5.2::DECIMAL(5,1)),
  ('h03', 'k2', TIMESTAMP '2026-10-21 14:01:00', 4.0::DECIMAL(5,1)),
  ('h06', 'k2', TIMESTAMP '2026-10-21 14:11:00', 8.5::DECIMAL(5,1)),
  ('h06', 'k2', TIMESTAMP '2026-10-21 14:11:00', 8.5::DECIMAL(5,1)),
  ('h07', 'k2', TIMESTAMP '2026-10-21 14:13:00', 8.9::DECIMAL(5,1)),
  ('h08', 'k3', TIMESTAMP '2026-10-21 14:12:00', 6.0::DECIMAL(5,1)),
  ('h09', 'k4', TIMESTAMP '2026-10-21 14:14:00', 2.0::DECIMAL(5,1))
) AS t(event_id, device_id, event_time, temperature);
-- What the last run left: one row per event, the 5-minute windows, and the windows over the limit.
CREATE TABLE silver.readings AS SELECT * FROM (VALUES
  ('h01', 'k1', TIMESTAMP '2026-10-21 14:00:00', 8.6::DECIMAL(5,1)),
  ('h02', 'k1', TIMESTAMP '2026-10-21 14:02:00', 9.0::DECIMAL(5,1)),
  ('h03', 'k2', TIMESTAMP '2026-10-21 14:01:00', 4.0::DECIMAL(5,1)),
  ('h04', 'k3', TIMESTAMP '2026-10-21 14:06:00', 8.8::DECIMAL(5,1))
) AS t(event_id, device_id, event_time, temperature);
CREATE TABLE gold.device_5min AS SELECT * FROM (VALUES
  ('k1', TIMESTAMP '2026-10-21 14:00:00', 2::BIGINT, 8.80::DECIMAL(6,2), 9.0::DECIMAL(5,1)),
  ('k2', TIMESTAMP '2026-10-21 14:00:00', 1::BIGINT, 4.00::DECIMAL(6,2), 4.0::DECIMAL(5,1)),
  ('k3', TIMESTAMP '2026-10-21 14:05:00', 1::BIGINT, 8.80::DECIMAL(6,2), 8.8::DECIMAL(5,1))
) AS t(device_id, window_start, readings, avg_temp, max_temp);
CREATE TABLE gold.alerts AS SELECT * FROM (VALUES
  ('k1', TIMESTAMP '2026-10-21 14:00:00', 8.80::DECIMAL(6,2)),
  ('k3', TIMESTAMP '2026-10-21 14:05:00', 8.80::DECIMAL(6,2))
) AS t(device_id, window_start, avg_temp);

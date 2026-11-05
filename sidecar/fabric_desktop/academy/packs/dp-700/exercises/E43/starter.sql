-- Build door_windows(kind, device_id, window_start, window_end, events) with:
--   kind 'tumbling': 5-minute blocks starting on the hour (10:00, 10:05, ...), end = start + 5 minutes
--   kind 'hopping' : 10-minute windows starting every 5 minutes, end = start + 10 minutes
--   kind 'session' : bursts where each event is at most 5 minutes after the one before;
--                    start = first event, end = last event + 5 minutes
-- One row per window that has at least one event.
CREATE OR REPLACE TABLE door_windows AS
SELECT ...;

SELECT * FROM door_windows ORDER BY kind, device_id, window_start;

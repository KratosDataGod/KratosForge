-- Build freezer_windows(kind, device_id, window_start, window_end, readings). A reading is warm when temp_c > -15.
--   kind 'sliding': at each warm reading, the device's warm readings in the 10 minutes that end at it.
--                   The window starts just after reading_ts - 10 minutes and ends at reading_ts, both of
--                   the same device. window_start = reading_ts - 10 minutes, window_end = reading_ts.
--                   Keep only the windows with 3 or more warm readings (the alarm).
--   kind 'episode': each run of warm readings in a row for a device, in reading order. A reading that
--                   isn't warm ends the run. window_start = first warm reading, window_end = last one.
CREATE OR REPLACE TABLE freezer_windows AS
SELECT ...;

SELECT * FROM freezer_windows ORDER BY kind, device_id, window_start;

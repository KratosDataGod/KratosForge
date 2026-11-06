-- The morning check: one row per item with its latest run, its usual duration and whether it got slower.
-- Columns: item_name, item_type, last_status, last_error, last_minutes, usual_minutes, slower. Order by item_name.
-- The usual duration is the median of the 7 successful runs before the latest one.
-- slower is true when the latest run succeeded and took more than 1.5 times the usual duration.
SELECT item_name, status, start_time, end_time
FROM run_history
ORDER BY start_time DESC;

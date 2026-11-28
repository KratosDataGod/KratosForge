-- The morning ingestion check: one row per Copy activity in copy_activities, from its latest run.
-- Columns: activity_name, last_start, last_status, rows_copied, rows_skipped, issue, last_error. Order by activity_name.
-- last_start, last_status, rows_copied, rows_skipped and last_error come from the activity's latest run.
--   rows_skipped is 0 when that run didn't report it; all five are NULL for an activity with no run at all.
-- issue is the first of these that applies:
--   'not run'      no run started in the 24 hours before ingestion_check.checked_at
--   'failed'       the latest run failed
--   'no rows'      the latest run succeeded and copied no rows
--   'rows skipped' the latest run succeeded and skipped incompatible rows
--   'ok'           otherwise
SELECT activity_name, status, rows_copied
FROM copy_runs
WHERE status = 'Failed'
ORDER BY activity_name;

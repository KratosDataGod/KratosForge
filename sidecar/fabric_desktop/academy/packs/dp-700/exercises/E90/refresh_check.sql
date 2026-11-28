-- The refresh check: one row per model.
-- Columns: model_name, storage_mode, last_status, last_good_start, consecutive_failures, schedule_disabled,
--   slow_refreshes. Order by model_name.
-- last_status: the status of the model's latest refresh.
-- last_good_start: the start_time of the model's latest Completed refresh. The model's data is as of then.
-- consecutive_failures: how many refreshes in a row have failed, counting back from the latest one.
-- schedule_disabled: true when the service has turned the schedule off. That happens after four failures in a
--   row, or at once when the latest refresh failed with an error that needs a configuration change
--   (error_type 'Credentials').
-- slow_refreshes: the model's Completed refreshes that took more than twice the median duration of that
--   model's own Completed refreshes.
SELECT model_name, refresh_id, status, start_time, end_time
FROM refresh_history
ORDER BY refresh_id DESC;

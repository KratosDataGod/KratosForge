-- The refresh check: one row per model.
-- Columns: model_name, last_status, last_error, fallback_queries, cause_table, cause. Order by model_name.
-- last_status and last_error come from the model's latest refresh.
-- fallback_queries counts queries that ran in DirectQuery when the model is set to fall back (Automatic).
-- cause and cause_table: 'guardrail' and the table when any table in the model is over an F64 guardrail (more
--   than 5,000 Parquet files, more than 5,000 row groups, or more than 1,500 million rows); otherwise 'sql view'
--   and the view the fallen-back queries read; otherwise NULL.
SELECT model_name, refresh_id, status, start_time
FROM refresh_history
ORDER BY refresh_id DESC;

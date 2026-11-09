-- The throttling check: one row per throttled hour.
-- Columns: hour_start, stage, top_item, top_item_kind, item_cu_s. Order by hour_start.
-- An hour is throttled when future_minutes goes over 10 in any of its timepoints. stage is the worst stage reached
-- in the hour: 'Interactive delay' (over 10 minutes), 'Interactive rejection' (over 60 minutes) or
-- 'Background rejection' (over 24 hours).
-- top_item is the item whose operations add the most smoothed CU to that hour; item_cu_s is that sum as a BIGINT.
-- Smoothing by whole hours: a background operation adds total_cu_s / 24 to its start hour and each of the next 23;
-- an interactive operation adds all of its total_cu_s to its start hour.
SELECT item_name, kind, start_time, total_cu_s
FROM operations
ORDER BY total_cu_s DESC;

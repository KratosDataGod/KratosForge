-- Fold stg_events into events and daily_sales so every day's total is right.
-- Keep 1 and 2 May as they are: their detail is archived.


SELECT sale_date, total FROM daily_sales ORDER BY sale_date;

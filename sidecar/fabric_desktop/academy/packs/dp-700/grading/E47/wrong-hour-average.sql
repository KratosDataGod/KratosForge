WITH hours AS (
    SELECT date_trunc('hour', timepoint) AS hour_start, avg(future_minutes) AS worst
    FROM throttle_log
    GROUP BY 1
),
throttled AS (
    SELECT hour_start,
           CASE WHEN worst > 1440 THEN 'Background rejection'
                WHEN worst > 60 THEN 'Interactive rejection'
                ELSE 'Interactive delay' END AS stage
    FROM hours
    WHERE worst > 10
),
smoothed AS (
    SELECT t.hour_start, o.operation_id, o.item_name, o.item_kind, CASE WHEN o.kind = 'Background' THEN o.total_cu_s / 24 ELSE o.total_cu_s END AS cu_s
    FROM operations AS o
    JOIN throttled AS t
      ON (o.kind = 'Interactive' AND date_trunc('hour', o.start_time) = t.hour_start)
       OR (o.kind = 'Background' AND t.hour_start >= date_trunc('hour', o.start_time)
           AND t.hour_start < date_trunc('hour', o.start_time) + INTERVAL 24 HOUR)
),
ranked AS (
    SELECT hour_start, o.item_name AS item_name, o.item_kind AS item_kind, sum(cu_s) AS cu_s,
           row_number() OVER (PARTITION BY hour_start ORDER BY sum(cu_s) DESC) AS rn
    FROM smoothed AS o
    GROUP BY hour_start, o.item_name, o.item_kind
)
SELECT t.hour_start, t.stage, r.item_name AS top_item, r.item_kind AS top_item_kind,
       CAST(round(r.cu_s) AS BIGINT) AS item_cu_s
FROM throttled AS t
JOIN ranked AS r ON r.hour_start = t.hour_start AND r.rn = 1
ORDER BY t.hour_start;

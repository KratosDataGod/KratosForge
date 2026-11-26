CREATE OR REPLACE TABLE freezer_windows AS
WITH warm AS (
    SELECT device_id, reading_ts FROM freezer_readings WHERE temp_c > -15
),
sliding AS (
    -- the window (reading_ts - 10 minutes, reading_ts]: the start is left out and the end is kept
    SELECT 'sliding' AS kind, w.device_id, w.reading_ts - INTERVAL '10 minutes' AS window_start,
           w.reading_ts AS window_end, count(*) AS readings
    FROM warm AS w
    JOIN warm AS o ON o.device_id = w.device_id
                  AND o.reading_ts > w.reading_ts - INTERVAL '10 minutes'
                  AND o.reading_ts <= w.reading_ts
    GROUP BY w.device_id, w.reading_ts
    HAVING count(*) >= 3
),
numbered AS (
    -- a session over warm readings: a gap over 5 minutes starts a new one, cold readings in between are ignored
    SELECT device_id, reading_ts, true AS is_warm,
           sum(CASE WHEN reading_ts - prev <= INTERVAL '5 minutes' THEN 0 ELSE 1 END)
               OVER (PARTITION BY device_id ORDER BY reading_ts) AS island
    FROM (SELECT *, lag(reading_ts) OVER (PARTITION BY device_id ORDER BY reading_ts) AS prev FROM warm)
),
episodes AS (
    SELECT 'episode' AS kind, device_id, min(reading_ts) AS window_start, max(reading_ts) AS window_end,
           count(*) AS readings
    FROM numbered WHERE is_warm
    GROUP BY device_id, island
)
SELECT * FROM sliding UNION ALL SELECT * FROM episodes;

SELECT * FROM freezer_windows ORDER BY kind, device_id, window_start;

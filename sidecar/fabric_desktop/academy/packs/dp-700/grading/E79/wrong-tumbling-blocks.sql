CREATE OR REPLACE TABLE freezer_windows AS
WITH warm AS (
    SELECT device_id, reading_ts FROM freezer_readings WHERE temp_c > -15
),
sliding AS (
    -- the window (reading_ts - 10 minutes, reading_ts]: fixed 10-minute blocks instead
    SELECT 'sliding' AS kind, device_id, time_bucket(INTERVAL '10 minutes', reading_ts) AS window_start,
           time_bucket(INTERVAL '10 minutes', reading_ts) + INTERVAL '10 minutes' AS window_end, count(*) AS readings
    FROM warm
    GROUP BY ALL
    HAVING count(*) >= 3
),
numbered AS (
    -- gaps and islands: within one run of warm readings both numbers go up together, so their
    -- difference stays the same. A reading that is not warm moves it on.
    SELECT device_id, reading_ts, temp_c > -15 AS is_warm,
           row_number() OVER (PARTITION BY device_id ORDER BY reading_ts)
         - row_number() OVER (PARTITION BY device_id, temp_c > -15 ORDER BY reading_ts) AS island
    FROM freezer_readings
),
episodes AS (
    SELECT 'episode' AS kind, device_id, min(reading_ts) AS window_start, max(reading_ts) AS window_end,
           count(*) AS readings
    FROM numbered WHERE is_warm
    GROUP BY device_id, island
)
SELECT * FROM sliding UNION ALL SELECT * FROM episodes;

SELECT * FROM freezer_windows ORDER BY kind, device_id, window_start;

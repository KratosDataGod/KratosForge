-- Session end is the last event, not last event + timeout.
CREATE OR REPLACE TABLE door_windows AS
WITH tumbling AS (
    SELECT 'tumbling' AS kind, device_id, time_bucket(INTERVAL '5 minutes', event_ts) AS window_start,
           time_bucket(INTERVAL '5 minutes', event_ts) + INTERVAL '5 minutes' AS window_end, count(*) AS events
    FROM door_events GROUP BY ALL
),
hopping AS (
    SELECT 'hopping' AS kind, device_id, ws AS window_start, ws + INTERVAL '10 minutes' AS window_end, count(*) AS events
    FROM (SELECT device_id, time_bucket(INTERVAL '5 minutes', event_ts) - to_minutes(o.back) AS ws
          FROM door_events, (VALUES (0), (5)) AS o(back))
    GROUP BY ALL
),
gaps AS (
    SELECT device_id, event_ts,
           CASE WHEN event_ts - lag(event_ts) OVER (PARTITION BY device_id ORDER BY event_ts) <= INTERVAL '5 minutes'
                THEN 0 ELSE 1 END AS starts_session
    FROM door_events
),
sessions AS (
    SELECT 'session' AS kind, device_id, min(event_ts) AS window_start,
           max(event_ts) AS window_end, count(*) AS events
    FROM (SELECT *, sum(starts_session) OVER (PARTITION BY device_id ORDER BY event_ts) AS session_no FROM gaps)
    GROUP BY device_id, session_no
)
SELECT * FROM tumbling UNION ALL SELECT * FROM hopping UNION ALL SELECT * FROM sessions;

SELECT * FROM door_windows ORDER BY kind, device_id, window_start;

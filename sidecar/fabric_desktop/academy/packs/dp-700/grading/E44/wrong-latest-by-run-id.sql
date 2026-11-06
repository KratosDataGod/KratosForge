WITH runs AS (
    SELECT *, date_diff('minute', start_time, end_time) AS minutes,
           row_number() OVER (PARTITION BY item_name ORDER BY run_id DESC) AS rn
    FROM run_history
),
latest AS (
    SELECT * FROM runs WHERE rn = 1
),
usual AS (
    SELECT item_name, CAST(median(minutes) AS DOUBLE) AS usual_minutes
    FROM (SELECT r.item_name, r.minutes,
                 row_number() OVER (PARTITION BY r.item_name ORDER BY r.start_time DESC) AS k
          FROM runs AS r
          WHERE r.rn > 1 AND r.status = 'Succeeded')
    WHERE k <= 7
    GROUP BY item_name
)
SELECT l.item_name, l.item_type, l.status AS last_status, l.error_message AS last_error,
       l.minutes AS last_minutes, u.usual_minutes,
       l.status = 'Succeeded' AND l.minutes > 1.5 * u.usual_minutes AS slower
FROM latest AS l
JOIN usual AS u USING (item_name)
ORDER BY l.item_name;

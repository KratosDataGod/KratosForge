INSERT INTO events
SELECT * FROM stg_events AS s
WHERE NOT EXISTS (SELECT 1 FROM events AS e WHERE e.event_id = s.event_id);

DELETE FROM daily_sales WHERE sale_date IN (SELECT DISTINCT event_date FROM stg_events);

INSERT INTO daily_sales
SELECT event_date, sum(amount)
FROM events
WHERE event_date IN (SELECT DISTINCT event_date FROM stg_events)
GROUP BY event_date;

SELECT sale_date, total FROM daily_sales ORDER BY sale_date;

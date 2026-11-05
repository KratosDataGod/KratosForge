-- Adds the batch's totals to yesterday's, so the retried e3 counts twice.
INSERT INTO events SELECT * FROM stg_events;

UPDATE daily_sales AS d SET total = d.total + b.total
FROM (SELECT event_date, sum(amount) AS total FROM stg_events GROUP BY event_date) AS b
WHERE d.sale_date = b.event_date;
INSERT INTO daily_sales
SELECT event_date, sum(amount) FROM stg_events
WHERE event_date NOT IN (SELECT sale_date FROM daily_sales) GROUP BY event_date;

SELECT sale_date, total FROM daily_sales ORDER BY sale_date;

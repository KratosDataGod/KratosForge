SELECT sum(amount) AS total
FROM sales
WHERE sold_at >= TIMESTAMP '2026-10-31 04:00'
  AND sold_at <  TIMESTAMP '2026-11-01 04:00';

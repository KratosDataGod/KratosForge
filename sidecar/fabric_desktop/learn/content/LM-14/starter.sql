SELECT sum(amount) AS total
FROM sales
WHERE CAST(sold_at AS DATE) = DATE '2026-10-31';

SELECT region, store, return_rate, discount,
       CASE WHEN return_rate >= 0.1 THEN 'review' ELSE 'ok' END AS tier
FROM (SELECT region, store,
             COALESCE(CAST(returns AS DECIMAL(9, 4)) / NULLIF(orders, 0), 0) AS return_rate,
             COALESCE(discount, 0) AS discount,
             row_number() OVER (PARTITION BY region
                                ORDER BY COALESCE(CAST(returns AS DECIMAL(9, 4)) / NULLIF(orders, 0), 0) DESC) AS n
      FROM store_stats) AS s
WHERE n = 1
ORDER BY region;

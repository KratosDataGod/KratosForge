SELECT region, store,
       DIV0(returns, orders) AS return_rate,
       ZEROIFNULL(discount) AS discount,
       IFF(DIV0(returns, orders) >= 0.1, 'review', 'ok') AS tier
FROM store_stats
QUALIFY row_number() OVER (PARTITION BY region ORDER BY DIV0(returns, orders) DESC) = 1
ORDER BY region;

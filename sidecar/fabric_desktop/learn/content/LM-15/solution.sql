SELECT region, month, amount,
       sum(amount) OVER (PARTITION BY region ORDER BY month
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total
FROM monthly_sales
ORDER BY region, month;

SELECT region, month, amount, sum(amount) OVER () AS running_total
FROM monthly_sales
ORDER BY region, month;

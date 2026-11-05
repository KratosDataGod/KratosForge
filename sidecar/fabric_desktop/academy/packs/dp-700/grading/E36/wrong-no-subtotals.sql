-- Plain GROUP BY: no region subtotals and no grand total.
SELECT region, category, sum(amount) AS total_amount
FROM gold_sales
GROUP BY region, category
ORDER BY region, category;

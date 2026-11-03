-- The right rows, but the subtotals come first because NULL sorts first here.
SELECT region, category, sum(amount) AS total_amount
FROM gold_sales
GROUP BY ROLLUP (region, category)
ORDER BY region NULLS FIRST, category NULLS FIRST;

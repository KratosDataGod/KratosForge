SELECT region, category, sum(amount) AS total_amount
FROM gold_sales
GROUP BY ROLLUP (region, category)
ORDER BY GROUPING(region), region, GROUPING(category), category;

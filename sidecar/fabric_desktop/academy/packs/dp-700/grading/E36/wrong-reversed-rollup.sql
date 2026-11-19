-- ROLLUP (category, region) rolls up the other way: subtotals per category instead of per region.
SELECT region, category, sum(amount) AS total_amount
FROM gold_sales
GROUP BY ROLLUP (category, region)
ORDER BY GROUPING(region), region, GROUPING(category), category;

-- GROUPING SETS with the detail and region levels only: the subtotals are there, the grand total isn't.
SELECT region, category, sum(amount) AS total_amount
FROM gold_sales
GROUP BY GROUPING SETS ((region, category), (region))
ORDER BY GROUPING(region), region, GROUPING(category), category;

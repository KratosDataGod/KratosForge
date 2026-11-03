-- CUBE adds a total for every category across regions, which the report didn't ask for.
SELECT region, category, sum(amount) AS total_amount
FROM gold_sales
GROUP BY CUBE (region, category)
ORDER BY GROUPING(region), region, GROUPING(category), category;

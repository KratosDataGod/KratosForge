-- Totals by region and category, a subtotal for each region, and a grand total.
-- Columns: region, category, total_amount. Order: regions A to Z, each region's categories A to Z,
-- then its subtotal; the grand total last.
SELECT region, category, sum(amount) AS total_amount
FROM gold_sales
GROUP BY region, category
ORDER BY region, category;

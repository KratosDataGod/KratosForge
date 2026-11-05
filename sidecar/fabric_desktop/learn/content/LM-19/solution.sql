SELECT product, sum(amount) AS revenue
FROM sales
WHERE region = 'North' AND sold_on >= DATE '2026-01-01' AND sold_on < DATE '2027-01-01'
GROUP BY product
ORDER BY product;

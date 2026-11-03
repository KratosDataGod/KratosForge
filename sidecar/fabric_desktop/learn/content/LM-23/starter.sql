CREATE TABLE daily_revenue AS
SELECT sold_on, sum(amount) AS revenue
FROM sales
GROUP BY sold_on;

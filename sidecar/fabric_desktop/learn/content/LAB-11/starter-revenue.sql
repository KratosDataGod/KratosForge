SELECT sold_on, sum(amount) AS revenue FROM sales GROUP BY sold_on ORDER BY sold_on;

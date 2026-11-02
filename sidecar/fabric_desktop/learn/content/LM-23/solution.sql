CREATE TABLE daily_revenue (day DATE NOT NULL, revenue DECIMAL(12, 2) NOT NULL);
INSERT INTO daily_revenue (day, revenue)
SELECT sold_on, sum(amount) FROM sales GROUP BY sold_on;

SELECT s.region, s.amount
FROM sales AS s
WHERE EXISTS (SELECT 1 FROM user_regions AS u
              WHERE u.user_name = 'ana@contoso.com' AND u.region = s.region)
ORDER BY s.region, s.amount;

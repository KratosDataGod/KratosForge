SELECT o.order_sk,
       COALESCE(c.customer_sk, -1) AS customer_sk,
       o.amount,
       o.net_amount
FROM silver_orders o
LEFT JOIN dim_customer c
       ON c.customer_dk = o.customer_sk
      AND c.customer_sk <> -1
      AND o.order_date >= c.valid_from
      AND o.order_date < c.valid_to
ORDER BY o.order_sk;

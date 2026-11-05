WITH latest AS (
  SELECT *
  FROM sales.order_events
  QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY updated_at DESC) = 1
),
live AS (
  SELECT * FROM latest WHERE status <> 'cancelled'
)
SELECT
  customer_id,
  order_id,
  order_date,
  amount,
  sum(amount) OVER (PARTITION BY customer_id ORDER BY order_date, order_id
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total,
  lag(amount) OVER (PARTITION BY customer_id ORDER BY order_date, order_id) AS previous_amount,
  dense_rank() OVER (PARTITION BY customer_id ORDER BY amount DESC) AS amount_rank
FROM live
ORDER BY customer_id, order_date, order_id;

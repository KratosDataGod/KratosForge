-- Apply customer_changes to dim_customer:
--   city is type 2 (close the current row on changed_on, add a new current row from changed_on),
--   email is type 1 (overwrite it on every row of the customer),
--   new customers get a new current row. New keys continue after max(customer_key), in customer_id order.


SELECT * FROM dim_customer ORDER BY customer_key;

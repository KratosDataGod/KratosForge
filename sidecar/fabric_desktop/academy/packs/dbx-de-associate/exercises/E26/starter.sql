-- E26: apply today's batch in bronze.customer_changes to silver.customers with one MERGE.
-- op is I (insert), U (update) or D (delete); a customer can change more than once in a batch, and
-- only their latest change counts. A rerun of the same batch must change nothing.
--
-- silver.customers keeps customer_id, name, city and updated_at (the change_ts of the change applied).

SELECT customer_id, op, name, city, change_ts
FROM bronze.customer_changes
ORDER BY customer_id, change_ts;

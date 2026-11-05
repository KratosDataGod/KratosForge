CREATE TABLE payments AS
SELECT payment_id, paid_on, CAST(amount AS DOUBLE) AS amount
FROM staging_payments;

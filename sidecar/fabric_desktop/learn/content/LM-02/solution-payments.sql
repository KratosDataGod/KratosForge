CREATE TABLE payments AS
SELECT CAST(payment_id AS INTEGER)     AS payment_id,
       CAST(paid_on AS DATE)           AS paid_on,
       CAST(amount AS DECIMAL(12, 2))  AS amount
FROM staging_payments
WHERE TRY_CAST(paid_on AS DATE) IS NOT NULL;

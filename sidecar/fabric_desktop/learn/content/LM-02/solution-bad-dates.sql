SELECT CAST(payment_id AS INTEGER) AS payment_id
FROM staging_payments
WHERE TRY_CAST(paid_on AS DATE) IS NULL
ORDER BY payment_id;

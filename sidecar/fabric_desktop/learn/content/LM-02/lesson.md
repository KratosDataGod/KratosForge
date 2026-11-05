# Data types, and why decimals beat floats for money

Every column has a type, and the type decides what the column can hold and how it compares and adds
up. Data often arrives as text. Turning it into the right types early catches bad values before they
reach a report.

## Text that should be a date

A date stored as text sorts and compares as text, so `'2026-9-1' > '2026-10-1'` is true: as text, 9
comes after 1. Cast it to `DATE` and it compares as a date. `CAST` stops with an error at the first value it can't read;
`TRY_CAST` gives an empty value (NULL) instead, which lets you find the bad rows first.

```sql
SELECT paid_on
FROM staging_payments
WHERE TRY_CAST(paid_on AS DATE) IS NULL;
```

## Money is a decimal

`DOUBLE` (a floating point number) stores binary fractions, so most cent amounts are close to the
value, not exactly it:

```sql
SELECT CAST(0.1 AS DOUBLE) + CAST(0.2 AS DOUBLE);                 -- 0.30000000000000004
SELECT CAST(0.1 AS DECIMAL(12, 2)) + CAST(0.2 AS DECIMAL(12, 2)); -- 0.30
```

(In T-SQL, the floating point type is `FLOAT`.)

`DECIMAL(12, 2)` keeps exactly two decimal places, up to 12 digits in all. Sums of money stay
exact, and an equality test such as `amount = 0.30` means what it says. Use `DOUBLE` for measurements
where tiny rounding doesn't matter, such as sensor readings or ratios.

The same advice holds in a semantic model: rule SM003 flags floating point columns there, and Fabric
Warehouse, Databricks and Snowflake all have a `DECIMAL` type for money.

## Try it

1. List the `payment_id` of every staging row whose `paid_on` isn't a real date, as a whole number,
   smallest first. February has no 30th.
2. Make a `payments` table from the rows that are good, with `payment_id` as `INTEGER`, `paid_on`
   as `DATE` and `amount` as `DECIMAL(12, 2)`.

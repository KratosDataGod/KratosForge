# Prepare a customer dimension

A dimension describes the things you measure: customers, products, stores. Before facts can point
at it, a dimension needs three things.

1. **One row per business key.** Staging copies often hold the same customer twice (a retried
   load, two exports). Remove exact duplicates before anything else.
2. **A surrogate key.** A whole number the warehouse owns (`customer_key`), separate from the
   source's id. Facts store the surrogate key, so a source that renumbers its customers, or two
   sources that use the same id, don't break your reports.
3. **An Unknown member.** A row with key 0 (or -1) that facts can point at when their customer is
   missing. Totals stay right and the report shows "Unknown" instead of dropping the sale.

Conform values through a lookup table (`ref_region`) rather than fixing spellings in each report:

```sql
SELECT c.customer_id, coalesce(r.region_name, 'Unknown') AS region
FROM stg_customers AS c
LEFT JOIN ref_region AS r ON r.region_code = lower(c.region);
```

In a Fabric Warehouse, surrogate keys are usually made with an `IDENTITY` column or by adding
`row_number()` to the current maximum key; here you'll number rows with `row_number()`.

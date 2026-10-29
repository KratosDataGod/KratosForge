# Keys: primary, foreign, business and surrogate

A key is the column (or columns) that tells rows apart. Getting keys right is what makes joins,
totals and history trustworthy.

## The four kinds

| Key | What it is | Example |
| --- | --- | --- |
| Business key | The id the business uses, from the source system | `customer_code = 'C-001'` |
| Surrogate key | A number the warehouse gives each row, with no meaning of its own | `customer_key = 1` |
| Primary key | The key that is unique and never empty in a table | `dim_customer.customer_key` |
| Foreign key | A column that points at another table's primary key | `fact_sales.customer_key` |

A dimension table (one row per customer) usually keeps both: the surrogate key as its primary key
and the business key so you can trace a row back to the source. A fact table (one row per sale)
refers to the dimension by its surrogate key.

## Why a surrogate key

Business keys change format, get reused, or clash when two sources are merged. A surrogate key is
small, stays the same, and joins quickly. When a customer's details change over time, a dimension
can keep one row per version, each with its own surrogate key and the same business key.

## Unique, and found

Two checks keep a model honest:

- The dimension's keys are unique and never empty. A business key that appears twice (here,
  `C-001`) would give a customer two surrogate keys.
- Every fact row finds its dimension row: no "orphans". A sale whose customer key isn't in the
  dimension disappears from any report that joins the two.

```sql
SELECT count(*) FROM fact_sales AS f
WHERE NOT EXISTS (SELECT 1 FROM dim_customer AS d WHERE d.customer_key = f.customer_key);
```

Fabric Warehouse lets you declare primary and foreign keys but does not enforce them (they are
`NOT ENFORCED`), so checks like these are how you know they hold.

## Try it

Make `dim_customer` with a surrogate `customer_key`, the business key `customer_code` (once each)
and `name`. Then make `fact_sales` with `sale_id`, `customer_key` and `amount`, where every sale
finds its customer.

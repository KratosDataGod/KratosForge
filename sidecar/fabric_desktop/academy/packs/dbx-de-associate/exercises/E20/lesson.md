# Clean bronze into silver with Databricks SQL

Exercise E19 cleaned `bronze.orders` with PySpark. The same work in SQL is one statement:
`CREATE OR REPLACE TABLE ... AS SELECT` (CTAS). The SELECT does the cleaning and the CREATE OR
REPLACE makes the write safe to repeat.

```sql
CREATE OR REPLACE TABLE silver.orders AS
SELECT ...
FROM bronze.orders
WHERE order_id IS NOT NULL;
```

Each column is cleaned in the SELECT list:

- **Types**: `CAST(trim(order_id) AS BIGINT)`. Trim before the cast, because `' 1003'` is not a number.
- **Two date formats**: a `CASE` that picks the format from the value's shape, such as
  `CASE WHEN order_ts LIKE '__/__/____' THEN to_date(order_ts, 'MM/dd/yyyy') ELSE ... END`. Wrapping two
  `to_date` calls in `coalesce` looks simpler, but with ANSI mode on (the default on a SQL warehouse) a
  value that doesn't fit the format is an error, not a NULL, so the first `to_date` stops the query.
- **Numbers with separators**: `replace(amount, ',', '')` before the cast, and `nullif(..., '')` so a
  blank becomes NULL. Turning a blank into 0 would invent revenue that doesn't exist.
- **Labels**: `lower(trim(status))`, with `coalesce(nullif(..., ''), 'unknown')` for a blank.
- **Duplicates**: `SELECT DISTINCT` drops rows that are exact copies. When copies differ (a later
  update), keep one row per key with `QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY ...) = 1`.

**Why CREATE OR REPLACE TABLE and not INSERT INTO.** `INSERT INTO silver.orders SELECT ...` adds the
rows again on every run, so a retried job doubles the table. CREATE OR REPLACE replaces the table's
contents in one Delta commit and keeps its history, so a reader never sees a half-written table and
you can still query the version before the run.

Your SQL is checked on the exercise data, again on rows you haven't seen, and once more after running
it twice. On this PC it is translated to DuckDB SQL and run there, in UTC: casts that would fail on a
warehouse fail here too, but nothing here shows Delta's history, Photon or Unity Catalog.

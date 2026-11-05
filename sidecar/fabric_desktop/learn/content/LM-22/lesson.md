# Schema drift: when a source adds a column

Sources change: a new column appears, one is renamed, a type widens. A pipeline that combines
files by position breaks, or worse, puts values in the wrong column without an error.

## Match by name

Name the columns you read from each batch, and give older batches a default for a column they
didn't have. Say why the default is right, here because January only sold in USD:

```sql
SELECT order_id, amount, 'USD' AS currency FROM batch_jan
UNION ALL
SELECT order_id, amount, currency FROM batch_feb;
```

DuckDB's `UNION ALL BY NAME` and Polars' `pl.concat(..., how="diagonal")` match by name and fill
missing columns with NULL. Delta tables in Fabric and Databricks refuse a write with new columns
unless you allow it (`mergeSchema`): turn that on only when the change is expected.

## Watch for it

Compare each batch's columns with the last one's before loading, and fail loudly on a rename or a
type change. A new column can often be added; a missing one usually means the source broke.

## Try it

Combine January's and February's orders now that February has a currency.

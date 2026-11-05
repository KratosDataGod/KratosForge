# MERGE an upsert with deletes

A change feed sends inserts, updates and deletes for a table. `MERGE INTO` applies a whole batch to a
Delta table in one commit: rows that match the key are updated or deleted, and rows that don't are
inserted.

```sql
MERGE INTO silver.customers AS t
USING changes AS s
ON t.customer_id = s.customer_id
WHEN MATCHED AND s.op = 'D' THEN DELETE
WHEN MATCHED THEN UPDATE SET name = s.name, city = s.city, updated_at = s.change_ts
WHEN NOT MATCHED AND s.op <> 'D' THEN INSERT (customer_id, name, city, updated_at)
  VALUES (s.customer_id, s.name, s.city, s.change_ts)
```

Three things make it right:

1. **One source row per key.** If a customer changed twice in the batch, two source rows match one
   target row. Databricks refuses that MERGE (`DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE`).
   Keep each key's latest change first:
   `QUALIFY row_number() OVER (PARTITION BY customer_id ORDER BY change_ts DESC) = 1`.
2. **Clause order.** `WHEN MATCHED` clauses are tried in order, so the conditional `DELETE` comes
   before the plain `UPDATE`. Without it, a delete becomes an update that blanks the name and city.
3. **Don't insert deletes.** A delete for a customer silver never had matches nothing; without
   `AND s.op <> 'D'` the insert clause adds an empty row for it.

**Why MERGE and not INSERT INTO.** Applying the same batch twice with MERGE leaves the same table: the
updates set the same values and the deletes find nothing left. An INSERT adds the rows again, and it
can't update or delete at all.

Your MERGE is checked on the exercise data, again on rows you haven't seen, and once more after
running it twice. On this PC it runs as DuckDB's MERGE. One difference matters here: DuckDB applies a
MERGE with several source rows per key instead of refusing it, so here that mistake shows up as a
wrong table, not as the error Databricks gives.

# MERGE: update, insert and delete in one step

A load often brings a batch of changes: some rows are new, some change rows already there, and some
say a row has gone. **MERGE** applies all three to a Delta table in one step, and one commit to
the table's log (LM-07), so a reader sees the table before the batch or after it, never half way.

```sql
MERGE INTO customers AS c
USING changes AS ch
ON c.customer_id = ch.customer_id
WHEN MATCHED AND ch.op = 'delete' THEN DELETE
WHEN MATCHED THEN UPDATE SET name = ch.name, city = ch.city
WHEN NOT MATCHED AND ch.op <> 'delete' THEN INSERT (customer_id, name, city)
                                           VALUES (ch.customer_id, ch.name, ch.city);
```

## What each clause does

| Clause | Applies to | Here |
| --- | --- | --- |
| `WHEN MATCHED AND ...` | A change whose key is in the table, and the extra condition | Deletes the row |
| `WHEN MATCHED` | Any other change whose key is in the table | Updates name and city |
| `WHEN NOT MATCHED` | A change whose key isn't in the table | Inserts it, unless it's a delete |

The clauses are tried in order, and each change uses the first one that fits. Rows in the table
that no change matches are left as they are. Databricks also has `WHEN NOT MATCHED BY SOURCE`, for
table rows the batch doesn't mention; this lesson doesn't use it.

## One row per key in the source

If two rows in `changes` have the same key, MERGE can't tell which one should win. Delta Lake
stops the MERGE with an error rather than guess. So make the source one row per key first, for
example by keeping each key's latest change (LM-06, LM-10).

## Check it before you run it

A MERGE changes the table, so it helps to know what it will leave behind. A `FULL OUTER JOIN`
on the key shows every key from both sides, which is exactly what MERGE looks at. This lab runs
in DuckDB on this PC and only reads: you write that SELECT, and the check compares it with the
table the MERGE above would leave.

## Try it

`customers` has four customers and `changes` has four changes. Write the SELECT that returns
`customers` as the MERGE would leave it: `customer_id`, `name` and `city`, smallest `customer_id`
first.

# Warehouse or lakehouse: where T-SQL writes

Fabric gives you T-SQL in two places, and only one of them writes.

| | Lakehouse SQL analytics endpoint | Warehouse |
| --- | --- | --- |
| Read tables with T-SQL | Yes | Yes |
| `CREATE TABLE`, `INSERT`, `UPDATE`, `DELETE` | No: views and functions only | Yes |
| Tables written by | Spark notebooks, pipelines, Dataflows | T-SQL, pipelines, Dataflows |

Both store Delta tables in OneLake, so a notebook can read a Warehouse table and a Warehouse query
can read a lakehouse table across items. Write each table from one place, and pick the item by
who builds it: SQL developers in a Warehouse, notebook developers in a lakehouse.

## Choose the types yourself

`SELECT ... INTO new_table` and `CREATE TABLE ... AS SELECT` let the query decide each column's
type and name, so a sum of money can become a float and a column keeps a source name you didn't
want. Rule SQ004 flags `SELECT ... INTO`. Create the table with the types you chose, then load it,
naming the columns on both sides (SQ010):

```sql
CREATE TABLE dbo.daily_revenue (day DATE NOT NULL, revenue DECIMAL(12, 2) NOT NULL);
INSERT INTO dbo.daily_revenue (day, revenue)
SELECT sold_on, sum(amount) FROM dbo.sales GROUP BY sold_on;
```

In the lab there's no `dbo.` schema; in a Warehouse, name it (SQ005).

## Try it

Create and load `daily_revenue` with the types the report needs.

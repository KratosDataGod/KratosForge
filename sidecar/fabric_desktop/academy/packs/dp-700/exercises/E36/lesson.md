# Subtotals with ROLLUP

A report often needs totals at more than one level: each region and category, each region, and
everything. Running three queries and gluing them together with UNION ALL works, but it reads the
table three times and the levels can drift apart.

`GROUP BY ROLLUP (region, category)` makes all three levels in one pass:

| Grouping | Rows |
| --- | --- |
| `(region, category)` | one per region and category |
| `(region)` | one subtotal per region, with `category` NULL |
| `()` | one grand total, with both NULL |

`CUBE` makes every combination, including totals per category across all regions. Use it only when
the report asks for them.

## Putting subtotals in the right place

A subtotal row has NULL in the columns it rolled up. Where NULL sorts differs between engines, so
don't rely on it. `GROUPING(column)` is 1 on a row where that column was rolled up and 0 otherwise:

```sql
ORDER BY GROUPING(region), region, GROUPING(category), category
```

puts each region's categories first, then its subtotal, and the grand total last, the same way in
DuckDB, Spark SQL and T-SQL.

## Try it

Change the starter so it adds a subtotal for each region and a grand total, in the order the comment asks for.

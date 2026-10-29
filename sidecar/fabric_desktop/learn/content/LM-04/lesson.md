# Grouping and aggregation

Most questions about data are totals: revenue per region, orders per day, the busiest store.
`GROUP BY` makes one row per group, and aggregate functions such as `count`, `SUM`, `AVG`, `MIN`
and `MAX` work out a value for each group.

## One row per group

```sql
SELECT region, count(*) AS sales, SUM(amount) AS total
FROM sales
GROUP BY region;
```

Every column in the `SELECT` list is either in `GROUP BY` or inside an aggregate. `GROUP BY`
already returns each group once, so `SELECT DISTINCT` on top of it only adds work; rule SQ009 flags
it.

## WHERE filters rows, HAVING filters groups

`WHERE` runs before grouping and keeps or drops single rows. `HAVING` runs after and keeps or drops
whole groups, so it can test an aggregate:

```sql
HAVING SUM(amount) > 500
```

Filter rows in `WHERE` when you can: fewer rows go into the grouping.

## Order the answer

Groups come back in no particular order. `ORDER BY total DESC` puts the largest first; you can
order by an alias from the `SELECT` list.

## Try it

For each region, show the number of sales (`sales`) and their total (`total`). Keep only regions
whose total is over 500, largest total first.

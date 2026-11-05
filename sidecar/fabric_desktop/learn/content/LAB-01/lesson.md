# From raw data to a checked answer

Every data pipeline starts the same way: take raw data, check it, clean it and turn it into
something people can use. This lab
has a small retailer's data, generated on this PC: customers, stores, products and a thousand
orders. Six problems were put into it on purpose, as real data has.

## The steps

1. **Look.** Count rows, and look at a few. In DuckDB, `SUMMARIZE orders` shows each column's
   range, empty values and approximate distinct counts in one go (it's DuckDB's own command).
2. **Find the problems.** Write one query that lists every bad row as `(table_name, key)`.
3. **Clean and transform.** Build the answer from the good rows only.
4. **Check the result.** Does it add up? Does every row still mean what it says?

## What to look for

| Problem | A query that finds it |
| --- | --- |
| A row loaded twice | `GROUP BY` the key `HAVING count(*) > 1` |
| A required value missing | `WHERE email IS NULL` |
| A value spelled differently | `WHERE region NOT IN ('North', 'South', 'East', 'West')` |
| An impossible value | a negative amount, or a date in the future |
| A row that points at nothing | `NOT EXISTS` in the table it refers to |

Finding them in a query, rather than by eye, means the same checks run every time the data
arrives. In a real pipeline they become data quality rules that stop or flag a load.

## Clean on purpose

Decide what each problem means for the answer. A cancelled or returned order isn't revenue. A
negative amount, a future date or an unknown store can't be trusted, so leave it out and report it.
A customer listed twice doesn't change revenue per customer, but it would double their orders if
you joined to `customers`.

## Try it

1. List every problem row, one row per problem, as `table_name` and `key`.
2. Show the ten customers with the most revenue from complete orders, leaving out the bad rows:
   `customer_id`, `orders` (how many), `revenue`, largest first, with `customer_id` breaking ties.

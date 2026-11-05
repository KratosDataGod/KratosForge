# Build a type 2 slowly changing dimension

A report that asks "where did this customer live when they bought?" needs the dimension to keep
history. A type 1 dimension overwrites the old value; a type 2 dimension keeps it, closes it, and
adds a new row.

| customer_key | customer_id | city | valid_from | valid_to | is_current |
| --- | --- | --- | --- | --- | --- |
| 1 | 101 | Seattle | 2025-01-01 | 2026-03-01 | false |
| 4 | 101 | Portland | 2026-03-01 | | true |

## The two steps

1. **Close** the current row of each customer whose tracked attribute really changed: set
   `valid_to` to the change date and `is_current` to false. A change row that repeats the current
   value isn't a change.
2. **Insert** a new current row for every change that now has no current row: the customers you
   just closed, and customers the dimension has never seen. Give each a new surrogate key (LM-05),
   because facts point at the version they belong to.

Run them in that order, in one transaction in production, so a report never sees a customer with
two current rows or none. Fabric Warehouse, Databricks and Snowflake run the same `UPDATE` and
`INSERT`; where a platform has `MERGE`, it can also do both steps in one statement (LM-11).

## The lab

`dim_customer` has three customers; `customer_changes` has a move, a repeat of the current city
and a new customer. Your answer can be several statements: the lab undoes them after each check.
End with `SELECT * FROM dim_customer ORDER BY customer_id, valid_from`.

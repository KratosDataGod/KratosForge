# Data quality checks in code

A check is an expectation about the data written so a machine can test it: "every order has a
customer", "no amount is negative", "order_id is unique". Run them on each batch before it moves
on, so a bad file stops at Bronze instead of reaching a report.

## Count failures, report them all

Make each check return how many rows break it, and run them all, so one run shows every problem:

```sql
SELECT 'amount_not_negative' AS check_name, count(*) AS failures FROM orders WHERE amount < 0
UNION ALL
SELECT 'customer_id_present', count(*) FROM orders WHERE customer_id IS NULL
```

In a notebook the same checks are a few lines of Polars or PySpark, or a library such as Great
Expectations; Databricks has table expectations built in. The idea doesn't change: a name, a rule,
a count.

## Stop or warn

Decide per check what a failure does. A duplicate key or a missing date usually stops the load;
an unusual but possible value may only warn. Write that decision down next to the check.

## Try it

Run three checks on an orders batch and list the ones that fail.

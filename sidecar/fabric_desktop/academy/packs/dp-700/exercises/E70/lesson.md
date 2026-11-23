# Running totals per customer with a PySpark window

Customer success wants to see how each customer's spend builds up over time: every order, with its place in that customer's history and the customer's total spend up to and including it. silver.orders has one row per order. Orders count in order_date order, and when a customer places two orders on the same day, the one with the lower order_id counts first, so each order gets its own number and its own running total.

## What you'll do

Aggregate over a window instead of a group: number each customer's orders and add a running total with Window.partitionBy().orderBy().rowsBetween(), keeping one row per order.

## What the table must hold

- One row per order in silver.orders, none dropped and none repeated.
- order_seq numbers each customer's orders 1, 2, 3 and on, by order_date and then order_id.
- running_amount is the customer's total amount over that order and every order before it in the same sequence.
- Write gold.customer_running_totals with customer_id, order_id, order_date, amount, order_seq, running_amount, so that running the code again leaves the same table.

## How it runs here

Your answer is PySpark code. It runs on this PC with DuckDB's PySpark API, not Apache Spark, on Delta tables in the exercise's own folder, with no sign-in and no network.

## Before you start

- E32

## What this run proves

- Your PySpark numbers and totals each customer's orders in the asked order, on the exercise data and on orders you haven't seen with three orders on one day, on this PC.
- gold.customer_running_totals has the columns asked for, one row per order_id and no empty values.
- Running it again leaves the same table.

## What it doesn't prove

- It doesn't run on Apache Spark or in a Fabric notebook, so it doesn't show how Spark shuffles the rows to each window's partition, or what happens when one customer holds most of the rows.
- That an order with no tie-break gives the same answer on every run: here the data always has order_id to break ties, and this PC's engine doesn't show Spark's run-to-run differences.
- rangeBetween over values (say, the last 7 days) and lag or lead, which this exercise doesn't use.
- That the table is a lakehouse table in OneLake: here it's a Delta table in the exercise folder on this PC.

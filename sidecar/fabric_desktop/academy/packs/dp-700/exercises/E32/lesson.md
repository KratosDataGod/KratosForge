# PySpark: clean, cast and join raw orders

Orders land in the Bronze lakehouse as text, straight from a CSV export: statuses in any case with stray spaces, some rows without a date or a quantity, and a few orders sent twice. Sales wants a Silver orders table with real types, one row per completed order and each product's category, including orders for products that aren't in the product list yet.

## What you'll do

Turn raw text orders into a typed Silver table with PySpark: tidy and filter on status, cast each column, drop incomplete and duplicate rows, and add each product's category with a left join.

## What the table must hold

- Only completed orders, whatever the case of status or the spaces around it.
- order_id and qty are integers, order_date is a date and amount is a decimal.
- No row without an order_date or a qty, and one row per order_id.
- category comes from bronze.products; an order whose product isn't there keeps its row with category 'unknown'.
- amount is qty times unit_price.
- Write silver.orders with order_id, order_date, product_id, category, qty, amount, so that running the code again leaves the same table.

## How it runs here

Your answer is PySpark code. It runs on this PC with DuckDB's PySpark API, not Apache Spark, on Delta tables in the exercise's own folder, with no sign-in and no network. In Fabric you'd run the same code in a notebook attached to a lakehouse.

## Before you start

- LM-08

## What this run proves

- Your PySpark cleans, casts, deduplicates and left-joins the exercise data and rows you haven't seen, on this PC.
- silver.orders has the columns asked for, one row per order_id and no missing key values.
- Running it again leaves the same table.

## What it doesn't prove

- It doesn't run on Apache Spark or in a Fabric notebook, so it doesn't show how Spark plans the join, how it behaves on large tables, or Spark's own casting rules for text it can't parse.
- That the table is a lakehouse table in OneLake: here it's a Delta table in the exercise folder on this PC.
- The SQL and KQL parts of the objective, which other exercises cover.

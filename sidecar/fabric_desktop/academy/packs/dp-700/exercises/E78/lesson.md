# A wide sales table with each customer as they were on the day

Sales analysts want one flat table to load into a notebook, with no joins to write. The customer dimension keeps history. When a customer moves region, the old row is closed and a new one starts on the same day. Finance reports each sale under the region the customer had on the day of the sale, not the region they have now. A sale can arrive for a customer or a product that isn't in its dimension yet, and those sales must still be in the table.

## What you'll do

Flatten a fact and its dimensions into one wide table with PySpark, taking a type 2 dimension's attributes from the version in force on each fact's date.

## What the table must hold

- One row per sale in silver.sales, none dropped and none repeated.
- customer_name, segment and region come from the customer version whose valid_from is on or before sale_date and whose valid_to is after it.
- product_name and category come from silver.dim_product.
- A sale whose customer or product has no matching row shows Unknown in each column that row would fill.
- Write gold.sales_wide with sale_id, sale_date, customer_id, customer_name, segment, region, product_name, category, amount, so that running the code again leaves the same table.

## How it runs here

Your answer is PySpark code. It runs on this PC with DuckDB's PySpark API, not Apache Spark, on Delta tables in the exercise's own folder, with no sign-in and no network. Check my work also runs it on sales you haven't seen.

## Before you start

- E35

## What this run proves

- Your PySpark picks the customer version in force on each sale's date and keeps sales with no matching customer or product, on the exercise data and on sales you haven't seen with three versions of one customer, on this PC.
- gold.sales_wide has the columns asked for, one row per sale_id and no empty attribute.
- Running it again leaves the same table.

## What it doesn't prove

- It doesn't run on Apache Spark or in a Fabric notebook, so it doesn't show how Spark plans a join on a date range or broadcasts a small dimension.
- Building the type 2 dimension itself. Here its rows are given.
- How the wide table performs in a Direct Lake semantic model compared with the star.
- That the table is a lakehouse table in OneLake. Here it's a Delta table in the exercise folder on this PC.

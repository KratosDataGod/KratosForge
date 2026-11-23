# Flatten nested JSON order events with PySpark

The shop's order API lands one JSON document per order in bronze.order_events: the order's id, customer and time, and an items array with each line's sku, quantity and price. Finance wants silver.order_lines with one typed row per order line. Some orders arrive with an empty items list, or with none at all, and Finance wants to see those too, so such an order keeps one row in silver.order_lines with sku, qty and price empty (NULL), never zero rows.

## What you'll do

Parse a JSON text column with from_json and a schema, turn each order's array of line items into one row per line with explode_outer, and write a typed Silver table without the raw JSON.

## What the table must hold

- One row per line item, and one row for an order whose items list is empty or missing, with sku, qty and price NULL.
- order_id and qty are integers, placed_at is a timestamp, price is a decimal and customer_id and sku are text.
- Every row has its order's order_id, customer_id and placed_at.
- Write silver.order_lines with order_id, customer_id, placed_at, sku, qty, price and no other column, so that running the code again leaves the same table.

## How it runs here

Your answer is PySpark code. It runs on this PC with DuckDB's PySpark API, not Apache Spark, on Delta tables in the exercise's own folder, with no sign-in and no network. Here from_json is reached through F.expr with the schema written as text, a form that also works in a Fabric notebook.

## Before you start

- E32

## What this run proves

- Your PySpark parses the JSON, flattens the line items and keeps orders without lines, on the exercise data and on orders you haven't seen with their keys in another order and extra fields, on this PC.
- silver.order_lines has exactly the columns asked for, with the types asked for, no empty order key, and one row per order and sku.
- Running it again leaves the same table.

## What it doesn't prove

- It doesn't run on Apache Spark or in a Fabric notebook. Here from_json is called through F.expr with the schema written as text, because F.from_json with a StructType isn't available on this PC. In a Fabric notebook both forms work.
- Malformed JSON: every document here parses. It doesn't show the corrupt-record column or the FAILFAST mode.
- Inferring the schema from the files with spark.read.json, and how Spark plans the explode on large tables.
- That the table is a lakehouse table in OneLake: here it's a Delta table in the exercise folder on this PC.
- The SQL and KQL parts of the objective, which other exercises cover.

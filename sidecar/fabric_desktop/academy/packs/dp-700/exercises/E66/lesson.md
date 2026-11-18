# Optimize a warehouse table: the smallest type that's still right for each column

A quick copy landed the sales feed in the warehouse with every column as text, and every report query now converts sale_date, qty and unit_price on the fly. Rebuild it as fact_sales with proper types. Store codes like '007' are labels from the store list, not amounts.

## What you'll do

Rebuild an all-text fact table with CREATE TABLE AS SELECT, choosing for each column the smallest exact type its values need, and spot the columns where the obvious type is wrong.

## Before you start

- E61
- E33

## What this run proves

- Your CREATE TABLE AS SELECT ran on this PC with DuckDB: fact_sales has the type the team's standard asks for in every column, every value survived the conversion, and fact_sales_raw is untouched.

## What it doesn't prove

- That queries get faster in a Fabric Warehouse: this checks the types and the values, not timings.
- Fabric Warehouse type names, which differ in places: there TIMESTAMP is datetime2 and BOOLEAN is bit.
- Statistics, result set caching and the other warehouse settings in E61.

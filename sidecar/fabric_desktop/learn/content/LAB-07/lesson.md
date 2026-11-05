# A Gold layer that stays in Direct Lake

Direct Lake models read Delta tables in OneLake straight into memory (LM-09). They stay fast only
while every table they read is a real Delta table and stays inside the capacity's limits. A
model built on a SQL view falls back to DirectQuery, which sends every visual's query to the SQL
endpoint instead.

## What Gold needs

- **Tables, not views.** Materialise the shape the report needs.
- **The grain the report uses.** Here, one row per day and product, not one per sale.
- **Keys that always match.** Every fact row points at a dimension row. A sale whose product
  isn't in the dimension goes to an Unknown member (key -1), so totals still add up and nobody
  loses rows to a blank.
- **Report-friendly types.** Dates as `DATE`, money as `DECIMAL`, keys as integers.

## The lab

`silver_sales` has sales by product code, including one code the dimension doesn't know.
`dim_product` already has the Unknown member. Build `gold_daily_sales` as a table.

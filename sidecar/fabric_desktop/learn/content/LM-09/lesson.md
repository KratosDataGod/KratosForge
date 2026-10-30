# Direct Lake: tables a Power BI model reads straight from OneLake

A Power BI semantic model can get its data three ways:

| Mode | How it reads | The catch |
| --- | --- | --- |
| Import | Copies the data into the model, at each refresh | A copy to refresh, and to fit in memory |
| DirectQuery | Sends a query to the source for each visual | Every click waits for the source |
| Direct Lake | Loads the Delta tables' Parquet columns from OneLake into memory as visuals need them | Needs tables shaped for it |

With Direct Lake there is no import copy. A refresh only points the model at the tables' latest
Delta versions, so it takes seconds. That makes the shape of your Gold tables matter more, because the
model reads them as they are.

## Tables that read well

- **A star.** One fact table per process (orders, payments) and dimension tables (date, customer,
  product) around it, joined on keys (LM-05).
- **Tables, not views.** A model on the SQL analytics endpoint that meets a SQL view can fall back
  to DirectQuery, which is slower. Build the shape into tables.
- **Compact files.** Many small files, or tables past the capacity's limits, also slow it or make
  it fall back. OPTIMIZE compacts them (LM-07). V-Order, a Parquet layout Fabric can write, also
  helps these reads.
- **Only the columns the model needs,** in the right types. Columns load into memory one at a
  time, when a visual uses them, so leave out the ones nobody reports on and keep keys as whole
  numbers.

## A date dimension

Reports filter and total by day, month and year. A `dim_date` table with one row per date, keyed by
a whole number such as `20260301`, gives the model one place for those columns and lets it mark the
table as a date table. The fact table stores the same whole-number `date_key`.

## Try it

The lab has a small retailer's orders, generated on this PC. Make `dim_date` (`date_key` INTEGER,
`date` DATE, `year`, `month`; one row per order date) and `fact_orders` (`order_id`, `date_key`
INTEGER, `customer_id`, `amount` DECIMAL), where every order finds its date.

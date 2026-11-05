# Optimize a slow query

The lab has two million generated orders in a DuckDB database on this PC. The starting query gives
the right answer, slowly. Your job is the same answer, at least twice as fast.

## How it's checked

- **Same answer.** Your rows must match the reference answer, in the same order.
- **No SQ002.** A function wrapped round a column in `WHERE` stops the engine skipping data.
- **Faster.** Your query and the starting one each run once to warm up, then five times taking turns, and each one's fastest run is compared.
  This is measured on this PC only. A Fabric Warehouse plans and runs queries differently, so the
  gain there may be larger or smaller.

## Where to look

Open the plan for the starting query. Look for steps that touch every row, and ask whether each
one is needed to answer the question. Hints are there if you want them, one at a time.

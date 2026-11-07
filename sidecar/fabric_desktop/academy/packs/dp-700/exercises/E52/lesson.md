# A notebook runs out of memory: collect() on a large table and a skewed join

A nightly notebook first failed when it pulled a whole table into Python, and after that was patched, its join to customers crawled on one task. The team needs to know what Spark does and what to change.

## What you'll do

Read the symptoms of a driver overload and a skewed join, and choose the cause and the fix for each.

## Notes

- toPandas() and collect() bring every row to the driver: use them only on small results, and keep large work in Spark, writing tables.
- Adaptive query execution splits skewed tasks in sort merge and shuffle hash joins into roughly even ones. A partition is skewed when it's more than 5 times the median and more than 256 MB.
- A broadcast hint, join(broadcast(small_df)), or spark.sql.autoBroadcastJoinThreshold makes Spark send a small table to every executor instead of shuffling the big one.
- In Fabric, the Spark advisor detects skew and shows an analysis.

## What this run proves

- You can pick the right feature, setting or operator for each scenario, with the reason and source shown afterwards.

## What it doesn't prove

- That you can do it in Fabric: nothing is set up or run here. Doing it on a real item is FABRIC VERIFIED work.

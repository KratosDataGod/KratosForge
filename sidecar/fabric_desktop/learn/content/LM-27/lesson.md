# Clustering and file pruning

Delta keeps the smallest and largest value of each column for every file. A query that filters
on a column opens only the files whose range could hold a match; the rest are skipped without
being read. That is file pruning, and it is most of what makes a filter on a big table fast.

## Why layout matters

Pruning only helps when values are grouped. If every file holds a whole month of dates, a filter
on one day still opens every file. Clustering rewrites the files so each holds a narrow range:

- **Liquid clustering** (Databricks, `CLUSTER BY (sold_on)`) keeps the table clustered as it
  grows, and the columns can change later.
- **Z-ordering** (`OPTIMIZE ... ZORDER BY`) clusters once, on Databricks and in Fabric Spark.
- **Partitioning** splits by folder; use it only for low-cardinality columns on very large tables.

The filter has to compare the bare column to use the ranges (SQ002).

## Try it

From the file statistics, list the files a one-day filter must open. Files 1 and 2 were written
before clustering; 3 to 5 after.

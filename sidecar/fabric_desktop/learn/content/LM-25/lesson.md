# V-Order, OPTIMIZE and VACUUM

A Delta table is Parquet files plus a log (LM-07). Each small write adds files, and every reader
opens every file, so a table loaded a few rows at a time slows down for everyone.

## OPTIMIZE and V-Order

`OPTIMIZE` rewrites many small files into fewer large ones (Fabric aims for about 128 MB to 1 GB
each) and records in the log that the old files are removed. Readers move to the new files at
once. V-Order is a Fabric write-time layout that makes Parquet faster for Power BI and SQL to
read. Fabric Spark can apply it as it writes (newer workspaces have it off by default;
`spark.sql.parquet.vorder.default` turns it on), and `OPTIMIZE ... VORDER` applies it to existing files.

## VACUUM

Removed files stay on disk so time travel and long-running readers still work. `VACUUM` deletes
the ones removed longer ago than the retention period, 7 days by default. A shorter retention
frees space sooner but can break a query or a time travel read still using those files, which is
why rule NB020 flags a short `VACUUM`.

## Try it

From a table's file list, count the small current files, then list what VACUUM would delete.

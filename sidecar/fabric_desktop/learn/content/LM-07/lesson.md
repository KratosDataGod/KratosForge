# OneLake and Delta tables: files and a log

**OneLake** is one data lake for your whole Fabric tenant. Each workspace is a folder in it, and
each lakehouse or warehouse is a folder in its workspace. A **shortcut** points at data somewhere
else, in another workspace or in a storage account, without copying it.

## A Delta table is files plus a log

A lakehouse table is a folder of Parquet files and a `_delta_log` folder beside them. Each change
to the table is a numbered **commit** in the log, and each commit says which files it **adds** and
which it **removes**. Files are never changed in place:

- An insert adds new files.
- By default, an update or delete adds files with the new rows and removes the files that held the
  old ones. (Tables with deletion vectors turned on can mark rows deleted instead.)
- **OPTIMIZE** adds a few large files and removes the many small ones they replace. Same rows,
  faster reads.

A **version** of the table is every file added up to that commit and not removed by then. That is how
time travel works: Spark SQL's `SELECT * FROM orders VERSION AS OF 2` reads the files that made up
version 2.

## VACUUM

Removed files stay in the folder, so older versions can still be read. `VACUUM` deletes the ones
removed longer ago than its retention period. The default is 7 days. A shorter retention can delete a
file that a query still running, or a time-travel read, needs. KratosForge's notebook checks
flag it (NB020).

## Try it

`delta_log` here is a small model of a real log: one row per file added or removed, with the
number of rows in each file. Version 2 added 15 rows and deleted 20, which rewrote `part-000` as
`part-003`. Version 3 was an OPTIMIZE.

1. List the files that make up version 2: `path` and `num_records`, by path.
2. Show how many rows the table had at each version: `version` and `row_count`, oldest first.

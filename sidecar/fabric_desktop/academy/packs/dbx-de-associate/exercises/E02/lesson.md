# Delta Lake under the hood: the log, history and time travel

**A table is files plus a log.** A Delta table is a folder of Parquet data files and a `_delta_log`
folder. Every change (a write, an update, a delete, a merge) adds one numbered JSON file to the log:
`00000000000000000000.json` is version 0, the next file version 1, and so on. Each file lists the data
files the change added and removed, and a `commitInfo` entry that names the operation. Readers build the
table from the log, so a half-finished write is never seen. That is what makes Delta tables ACID.

**History.** The log is the table's history. In Databricks, `DESCRIBE HISTORY products` lists each
version with its operation, time and user. On this PC, delta-rs gives the same list:

```python
deltalake.DeltaTable('tables/products').history()   # newest first: version, operation, ...
```

**Time travel.** An earlier version is still readable as long as its files haven't been vacuumed. In
Databricks SQL that is `SELECT * FROM products VERSION AS OF 0` (or `TIMESTAMP AS OF`); with delta-rs:

```python
old = deltalake.DeltaTable('tables/products', version=0)   # read it with read(old) in the starter
```

**Undo, carefully.** `RESTORE TABLE products TO VERSION AS OF 0` puts the whole table back the way it
was, which also undoes every good change made since. When only one change was wrong, read the earlier
version, take what that change broke, and write the fix as a new version. The history then shows the
mistake and the repair, and nothing else is lost.

**On this PC.** delta-rs reads and writes the same Delta format Databricks does. What it doesn't show:
Unity Catalog's managed tables and permissions, `VACUUM` retention rules on your workspace, deletion
vectors and Photon.

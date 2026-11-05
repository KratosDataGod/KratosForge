# Time travel and restoring a table

Delta and Snowflake tables keep their history, so you can read a table as it was before a bad
load or delete, and put it back, without a separate backup.

| | Snowflake | Delta (Fabric, Databricks) |
| --- | --- | --- |
| Read the past | `SELECT ... AT(OFFSET => -3600)` | `SELECT ... VERSION AS OF 2` or `TIMESTAMP AS OF '...'` |
| Put it back | `CREATE TABLE ... CLONE ... AT(...)` | `RESTORE TABLE orders TO VERSION AS OF 2` |
| How long | Time Travel retention (1 day by default) | Until `VACUUM` deletes the old files (7 days by default, LM-25) |

`DESCRIBE HISTORY orders` lists Delta versions with who changed what. A Fabric Warehouse has time
travel too, with `OPTION (FOR TIMESTAMP AS OF ...)`.

## Before you restore

Find the version just before the mistake, read it, and check it's what you expect. A restore is
itself a new version, so it can be undone too.

## Try it

DuckDB has no table history, so the lab keeps each version as rows of `table_versions`. Read
version 2.

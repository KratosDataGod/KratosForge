# Local data projects (Preview)

A local data project is a SQL database on your PC that reads the data files in folders you register. Point it at a folder of CSV, JSON and Parquet files and every file is a table you can query straight away. Tables and views you create are kept, so you can close KratosForge, open it again and carry on. The project can also write Delta tables, laid out as a Fabric lakehouse lays them out, and read any Delta table as it was at an earlier version. Nothing here signs in to Fabric or uses capacity.

This is the first part of the [local data engine program](local-data-engine-plan.md). The engine works now; the screens for it come next, after the mockup is agreed. Until then the engine is reached through its calls (below), which is also how tests and external tools use it.

## What you can do

- Create several projects, each with its own database. A project kept **on disk** (the default) keeps its tables between sessions; a **memory** project keeps only its list of folders.
- Register folders. Files are found in the folder and in sub-folders (4 levels by default), and each becomes a table.
- Query with DuckDB SQL, or Spark SQL (translated with sqlglot, as on the SQL page): joins, `GROUP BY`, CTEs, window functions, `CREATE TABLE ... AS SELECT`, `INSERT`, `UPDATE`, `DELETE`, views, and temporary tables that last until the project is closed.
- Set a memory limit, a thread count, a time limit for queries, and how much may spill to disk. With a memory limit, DuckDB writes to the project's `tmp` folder instead of running out of memory, so a query can work through more data than fits in RAM.
- Cancel a running query.
- Write [Delta tables](#delta-tables) from SQL, add to them, replace them, and read earlier versions.

## How files become tables

Files are named the way [DaffyDB](https://www.nuget.org/packages/DaffyDB/) names them:

| On disk | Table |
| --- | --- |
| `orders.parquet` | `dbo.orders` |
| `Sales.Customer.csv` | `Sales.Customer` (a dotted name sets the schema) |
| `Sales/Customer.csv` | `Sales.Customer` (a folder sets the schema) |
| `inventory/part-00000.parquet`, `part-00001.parquet` | `dbo.inventory` (a folder of part files is one table) |
| `events/year=2026/month=10/*.parquet` | `dbo.events`, with `year` and `month` columns |
| `sales/_delta_log/...` | `dbo.sales`, a Delta table at its current version |

- Formats: Parquet, CSV and TSV, JSON (an array or one object per line: `.json`, `.jsonl`, `.ndjson`), and compressed CSV and JSON (`.gz`, `.zst`). Excel workbooks are found and listed, but can't be read yet.
- `dbo` is the default schema, as in Fabric: `orders` and `dbo.orders` are the same table, and `CREATE TABLE t` makes `dbo.t`.
- With `naming = "flat"`, folders don't set the schema: `Sales/Customer.csv` is `dbo.Sales_Customer`. A different default schema, or a name for one file (`names = {"sales/2026.csv": "raw.sales_2026"}`), can be set per folder.
- When two files would get the same name, Parquet wins over CSV and CSV over JSON, and the other file is listed as a conflict. A file never replaces a table or view you created.
- Skipped: hidden and dot folders, `node_modules`, `bin`, `obj`, `__pycache__`, virtual environments, build folders, and configuration files such as `package.json`. Add more with `exclude`.
- Column types are inferred. Override them for a table (for example, read `id` as `VARCHAR`).
- A file that can't be read is listed with the reason; the other files still work.
- Changed files are picked up before the next query (checked at most every 2 seconds). Switch this off with `auto_refresh = false` and refresh when you choose.

Delta tables that use deletion vectors, column mapping or other features a plain read would get wrong are listed with the reason and not queryable yet. Nothing in a registered folder is ever written or deleted: removing a folder from a project drops only its views.

## Delta tables

A project writes Delta tables into its own `Tables` folder, one folder per table, as a schema-enabled lakehouse does (`Tables\dbo\sales`). They are views in the project like any other table, and every write is one Delta commit made with delta-rs.

```sql
-- Create one (fails if it exists; IF NOT EXISTS skips it, OR REPLACE gives it a new schema)
CREATE TABLE sales USING DELTA PARTITIONED BY (year) AS
SELECT *, year(order_date) AS year FROM dbo.orders;

INSERT INTO sales SELECT * FROM dbo.new_orders;      -- add rows
INSERT INTO sales BY NAME SELECT amount, id FROM t;  -- match columns by name, not position
INSERT OVERWRITE sales SELECT * FROM sales WHERE year >= 2025;  -- replace every row
```

The same statements work in Spark SQL (`INSERT INTO TABLE sales ...`, `INSERT OVERWRITE TABLE sales ...`).

**Earlier versions.** Every write is a new version, and the old ones stay readable until the files are vacuumed:

| Dialect | Version | Time |
| --- | --- | --- |
| Spark SQL | `SELECT * FROM sales VERSION AS OF 3` | `SELECT * FROM sales TIMESTAMP AS OF '2026-10-01 14:00'` |
| DuckDB SQL | `SELECT * FROM sales AT (VERSION => 3)` | `SELECT * FROM sales AT (TIMESTAMP => '2026-10-01 14:00')` |

Times are UTC. A time picks the last version committed at or before it. To restore a version, overwrite from it: `INSERT OVERWRITE sales SELECT * FROM sales AT (VERSION => 3)`, which is a new version itself. Delta tables in registered folders can be read as of a version too.

**Fabric-safe.** Tables are written in the plain Delta profile Fabric reads reliably (reader version 1, writer version 2, no table features), checked before and after every write, so a later publish to OneLake needs no conversion:

- `TIMESTAMP` columns are stored as UTC timestamps. (A Delta `timestamp_ntz` column would need reader version 3.)
- Unsigned integers widen to the next signed type (`UTINYINT` to `SMALLINT`, `UBIGINT` to `DECIMAL(20,0)`).
- Types Delta has no column for are refused with what to cast them to: `TIME`, `INTERVAL`, decimals over 38 digits.
- Column names can't have spaces or `,;{}()=`, and can't differ only in case.
- A table whose own protocol is outside that profile (for example one written elsewhere with deletion vectors) isn't written to.

**Schemas.** Rows added to a table must have its columns. `INSERT` matches them by position, as SQL does; `BY NAME` and the `local_write_delta` call match them by name. A value goes into another column type of the same kind (a number into any number column, a date into a timestamp column); one that doesn't fit, such as 70000 in a `SMALLINT`, stops the write. New or missing columns are a schema change: `local_write_delta` with `schema_mode = "merge"` adds them, and `CREATE OR REPLACE TABLE` (or `schema_mode = "overwrite"`) gives the table a new schema and partitioning.

**Nothing half-written.** A write that fails, is cancelled or runs out of time commits nothing, and a table that was being created leaves no folder behind. A dry run (`local_write_delta` with `dry_run = true`) runs the query and reports the rows, columns and schema change, and writes nothing.

**What stays read-only.** Delta tables in registered folders are never written: copy one into the project with `CREATE TABLE mine USING DELTA AS SELECT * FROM theirs`. Deleting a project table deletes its folder and every version, so it isn't done in SQL (`DROP TABLE` on one is refused) but with `local_drop_delta`, which only says what it would delete until it is called again with `confirm`.

**Changing rows: MERGE, UPDATE and DELETE.** The project's Delta tables take the statements Fabric and Databricks notebooks use, in either dialect, each in one commit:

```sql
MERGE INTO customers AS t USING updates AS s ON t.id = s.id
  WHEN MATCHED AND s.deleted THEN DELETE
  WHEN MATCHED THEN UPDATE SET *                  -- or SET t.name = s.name, ...
  WHEN NOT MATCHED THEN INSERT *                  -- or INSERT (id, name) VALUES (s.id, s.name)
  WHEN NOT MATCHED BY SOURCE THEN DELETE;         -- rows the source no longer has
UPDATE customers SET spend = spend * 1.1 WHERE region = 'EU';
DELETE FROM customers WHERE last_order < '2020-01-01';
```

- The source (`USING`) is any query or table in the project, and can read a table as it was (`VERSION AS OF`). Column names match without regard to case, as in Spark.
- As in Spark, a MERGE in which two source rows match the same table row is refused, because which one would win is undefined. Keep one row per key first (`QUALIFY row_number() OVER (PARTITION BY id ORDER BY updated DESC) = 1`). This is checked when `ON` is `t.col = s.col [AND ...]`.
- An `UPDATE` or `DELETE` without `WHERE` changes every row, so it isn't run from a query. Run it with `local_change_delta` and `confirm`.
- `local_change_delta` with `dry_run` runs the statement on a copy of the table (its log copied, its data files linked, deleted afterwards) and returns exactly how many rows it would insert, update and delete. The table isn't touched.
- Conditions and values are run by delta-rs, so a subquery inside one (`WHERE id IN (SELECT ...)`) isn't supported. Join the rows in with `MERGE ... USING (SELECT ...)` instead. `UPDATE ... FROM` and `DELETE ... USING` are refused for the same reason.
- `MERGE ... WITH SCHEMA EVOLUTION` (adding columns from the source) isn't supported yet.

A Delta write isn't part of a DuckDB transaction: `ROLLBACK` doesn't undo it. Only one program should write a project's Delta tables at a time, as with its database.

## Where it is kept

Each project is a folder under `%LOCALAPPDATA%\FabricDesktop\local-data\` (or one you choose, for example inside a Git repository):

| File | Holds |
| --- | --- |
| `project.json` | Name, settings, registered folders, type overrides and the list of discovered tables |
| `data.duckdb` | The database: your tables and views, and views over the discovered files |
| `tmp\` | Data DuckDB spills to disk under a memory limit |
| `Tables\<schema>\<table>\` | The project's Delta tables. Tables copied in here are picked up when the project opens or is refreshed. |

Only one program can write to a project's database at a time. If another KratosForge window or a notebook has it open, the project says so instead of risking the file; close it there (or close the project) and try again.

## Engine calls

| Call | Does |
| --- | --- |
| `list_local_projects` | Every project, with its settings and folders |
| `local_create_project(name, storage, folder, settings)` | A new project (`storage`: `persistent` or `memory`) |
| `local_project_settings(project, settings)` | `memory_limit` (`4GB`, `60%`), `threads`, `timeout_seconds`, `max_temp_size`, `auto_refresh` |
| `local_add_source(project, path, schema, naming, max_depth, exclude, names)` | Register a folder; returns its tables and any problems |
| `local_refresh(project, path)` / `local_remove_source(project, path)` | Look again / stop reading a folder |
| `list_local_tables(project)` | Schemas, tables, views, temporary tables and columns, with each file's format, size and status |
| `local_set_types(project, table, columns)` | Read a discovered table's columns as these types |
| `local_query(project, query, dialect, limit, timeout)` | Run SQL and return columns, up to `limit` rows, the row count, whether rows were left out, and timings per statement |
| `local_query_start` / `local_query_status` / `local_query_cancel` | The same, in the background, for long queries |
| `local_write_delta(project, table, query, mode, partition_by, schema_mode, dry_run)` | Write a query's rows to a Delta table in the project (`mode`: `create`, `append` or `overwrite`) |
| `local_change_delta(project, query, dialect, dry_run, confirm)` | Run one `MERGE INTO`, `UPDATE` or `DELETE` on a Delta table in the project; returns rows inserted, updated and deleted. `dry_run` changes nothing; `confirm` is needed for every row |
| `local_delta_history(project, table, limit)` | A Delta table's versions: time, operation, rows and files added |
| `local_drop_delta(project, table, confirm)` | Delete one of the project's Delta tables with every version (only with `confirm`) |
| `local_close_project` / `local_forget_project` | Close the database / take the project off the list (its folder stays) |

In Python, `LocalEngine.execute_reader(sql)` streams a query's whole result in Arrow batches with no row limit; the SQL page's managed execution reads results that way. Read the stream to the end or close it (`with engine.execute_reader(sql) as stream:`), because the project answers one request at a time until then.

Each query and change is recorded in the [audit trail](user-guide.md#audit-trail) without its SQL text.

## Not yet

Excel, T-SQL, `MERGE` in notebook cells (`spark.sql` on this PC still refuses it), publishing to OneLake, a SQL Server-compatible endpoint, Power BI Desktop and the screens are later steps in the [plan](local-data-engine-plan.md#4-pr-plan). Delta tables are untested against a Fabric lakehouse until a publish exists; their profile is the one the app's other writes to OneLake already use.

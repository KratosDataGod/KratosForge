# Local data projects (Preview)

A local data project is a SQL database on your PC that reads the data files in folders you register. Point it at a folder of CSV, JSON and Parquet files and every file is a table you can query straight away. Tables and views you create are kept, so you can close Fabric Desktop by KratosBI, open it again and carry on. Nothing here signs in to Fabric or uses capacity.

This is the first part of the [local data engine program](local-data-engine-plan.md). The engine works now; the screens for it come next, after the mockup is agreed. Until then the engine is reached through its calls (below), which is also how tests and external tools use it.

## What you can do

- Create several projects, each with its own database. A project kept **on disk** (the default) keeps its tables between sessions; a **memory** project keeps only its list of folders.
- Register folders. Files are found in the folder and in sub-folders (4 levels by default), and each becomes a table.
- Query with DuckDB SQL, or Spark SQL (translated with sqlglot, as on the SQL page): joins, `GROUP BY`, CTEs, window functions, `CREATE TABLE ... AS SELECT`, `INSERT`, `UPDATE`, `DELETE`, views, and temporary tables that last until the project is closed.
- Set a memory limit, a thread count, a time limit for queries, and how much may spill to disk. With a memory limit, DuckDB writes to the project's `tmp` folder instead of running out of memory, so a query can work through more data than fits in RAM.
- Cancel a running query.

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

## Where it is kept

Each project is a folder under `%LOCALAPPDATA%\FabricDesktop\local-data\` (or one you choose, for example inside a Git repository):

| File | Holds |
| --- | --- |
| `project.json` | Name, settings, registered folders, type overrides and the list of discovered tables |
| `data.duckdb` | The database: your tables and views, and views over the discovered files |
| `tmp\` | Data DuckDB spills to disk under a memory limit |

Only one program can write to a project's database at a time. If another Fabric Desktop window or a notebook has it open, the project says so instead of risking the file; close it there (or close the project) and try again.

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
| `local_close_project` / `local_forget_project` | Close the database / take the project off the list (its folder stays) |

In Python, `LocalEngine.execute_reader(sql)` streams a query's whole result in Arrow batches with no row limit; the SQL page's managed execution reads results that way. Read the stream to the end or close it (`with engine.execute_reader(sql) as stream:`), because the project answers one request at a time until then.

Each query and change is recorded in the [audit trail](user-guide.md#audit-trail) without its SQL text.

## Not yet

Excel, T-SQL, writing local Delta tables, publishing to OneLake, a SQL Server-compatible endpoint, Power BI Desktop and the screens are later steps in the [plan](local-data-engine-plan.md#4-pr-plan).

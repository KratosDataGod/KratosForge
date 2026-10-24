# Warehouses (Preview)

KratosForge is building a full development experience for Fabric warehouses, then Snowflake: query, explore, template, save, build and deploy. This page covers what ships today and grows with each stage. The program's plan and order are in the [warehouse proposal](#the-plan).

## The Warehouse page

In **Workspaces**, open a workspace and press **SQL query** on a **Warehouse** or **SQL analytics endpoint** row. The Warehouse page opens: the SQL editor, the compatibility check and the managed results you know from lakehouse queries, with the warehouse as the target.

- **It runs in Fabric and uses capacity.** Unlike a lakehouse query, which runs on this PC, a warehouse query runs on the warehouse. The first run on each warehouse after the app starts asks you to confirm.
- **It only reads.** One statement that reads is sent. DDL, DML, `EXEC`, variables and anything that reaches outside the warehouse (`OPENROWSET`) are refused before Fabric is asked anything. Changing a warehouse from the app is a later stage and waits on its own decision.
- **It runs as you.** The app signs in to Azure SQL (`https://database.windows.net`) with your account, the first time you run a warehouse query, and Fabric's permissions on the warehouse apply as they do in Fabric. See [permissions](permissions.md).
- **Same run controls.** Stop, the time limit in Settings › SQL, results a page at a time, Copy and Export to CSV or Parquet. The `sql_execution` audit line names the workspace and warehouse, never the SQL.
- **No risk check, by design.** The [risk check](finops/risk-check.md) looks at workloads (notebooks, pipelines, Data mesh runs) before a run. A warehouse query isn't a workload: it only reads, and the confirm, Stop and the time limit bound what it can use. The cost of warehouse queries comes with query insights (WH-07) and, later, Optimize.
- **Check before you run.** The editor checks T-SQL against Fabric Warehouse (or the SQL analytics endpoint) as you type, from Microsoft's [T-SQL surface area](https://learn.microsoft.com/en-us/fabric/data-warehouse/tsql-surface-area) page. See [SQL compatibility](sql-compatibility.md).

## Explorer

The left side lists the warehouse's schemas, and in each its **Tables**, **Views**, **Stored procedures** and **Functions**, read from the warehouse's own catalog views (`sys.objects`, `sys.columns`, `sys.types`). Listing runs two catalog queries in Fabric, so it uses a little capacity: it starts by itself once you have confirmed a run on that warehouse this session, otherwise **List objects** asks first.

- Click a table or view to run `SELECT TOP (100) *` on it (in the current tab when it's empty, else a new one).
- Open a table or view to see its columns with their T-SQL types, such as `decimal(18,2)` or `nvarchar(100)`. Click a column, procedure or function to type its quoted name in the editor.
- **Script as** (an object's **⋯** button, or right-click): **Select top 100 rows**, **Script as CREATE**, **Script as ALTER** (views, procedures and functions) and **Script as DROP**. The script opens in a new tab and never runs: this page runs only queries that read. A table's CREATE is built from the columns already listed, so it asks Fabric nothing. A view, procedure or function's definition is read from `sys.sql_modules` (one catalog query in Fabric), and ALTER turns its leading `CREATE` or `CREATE OR ALTER` into `ALTER`. A table's CREATE carries column names, types and NULL or NOT NULL only, not constraints.
- **Filter objects** narrows the tree by name or schema. **Refresh** lists them again.
- The editor completes table, view and column names from the same list, without asking Fabric again.

A SQL analytics endpoint is found through its lakehouse, which Fabric gives the same name in the same workspace. If the lakehouse isn't in the list, the row has no **SQL query** button; use **Open in Fabric**.

## SQL from a template

**SQL from a template** on the Warehouse page opens a gallery of T-SQL for Fabric Warehouse, in the groups Fabric's own *New SQL query from template* uses plus accelerators:

| Group | Templates |
| --- | --- |
| Tables | Create a table, Table from a query (CTAS), Add a column, Primary key (not enforced) |
| Load data | Load files with COPY INTO, Append from a lakehouse table, Truncate and reload |
| Views and functions | Create or alter a view, Inline table-valued function |
| Stored procedures | Stored procedure with parameters |
| Time travel and clone | Query a table as it was, Clone a table |
| Security | Grant read on a schema, Column-level security, Row-level security, Dynamic data masking |
| Statistics | Create and update statistics |
| Accelerators | SCD type 2 merge procedure, Incremental load with a watermark, Dimension with surrogate keys, Data quality checks |

Pick a template, fill in its fields (schema, table, columns and the like) and watch the script change beside them. **Open in a new tab** puts the script in a new query tab. Opening a template runs nothing.

- **Fields are checked for what they hold.** A name can't contain `]` or a line break and is written inside `[ ]`; a column list becomes `[a], [b]`; text is written inside quotes with `'` doubled; numbers, types (`varchar(50)`, `decimal(18,2)`) and dates (`yyyy-mm-ddThh:mm:ss`) must look like one. Until every field is fine, **Open in a new tab** stays off, so a half-filled script never reaches the editor.
- **Search** finds a template by its title, description or group.
- **On a SQL analytics endpoint**, which only reads, the gallery offers only the templates that work there: views, functions, procedures, security and data quality checks.
- **Only a query that reads runs on this page.** Most templates create or change objects or data, which the page refuses until warehouse writes ship (a later stage, waiting on its own decision). For now, copy those scripts into Fabric's SQL editor.
- Every template is checked against the Fabric Warehouse rules of the [compatibility check](sql-compatibility.md) by the app's tests. Two say *Review* (Truncate and reload, Dynamic data masking) and two *Not verified* (time travel's `OPTION (FOR TIMESTAMP AS OF ...)` and `CREATE SECURITY POLICY`, which the checker can't read).

## Results, Messages and Chart

Above the result, three views, on every SQL page (lakehouse, local project and warehouse). Messages and Chart are Preview: on a lakehouse or local project page they carry the Preview tag and go when Settings hides preview features; on the Warehouse page the whole page is already Preview.

- **Results**: the rows a page at a time, with Copy and Export, as before.
- **Messages**: what the last run did: its target, when it was queued, started and ended, its state, rows and columns, why it was refused or failed, and its time limit. Never the SQL.
- **Chart**: a quick bar, stacked bar, line, area, pie, scatter or histogram of the result, with X axis, values, aggregate (sum, average, count, max, min) and series. It reads the rows already on this PC from the run's snapshot, up to the first 50,000, so nothing runs again and no capacity is used. It draws the first 60 X axis values (the note says how many more there are) and up to 7 series (the rest as Other).

A query that returns several result sets isn't possible yet: the page sends one statement at a time. That comes with warehouse writes and scripts, which wait on their own decision.

The page is tagged Preview until a query has been run on a tenant. Settings can hide preview features.

How a run works underneath (states, limits, what is refused): [Running SQL queries](sql-execution.md#queries-on-a-warehouse-preview).

## The plan

| Stage | What it adds | State |
| --- | --- | --- |
| WH-01 | The Warehouse page and its warehouse target | Done |
| WH-02 | Object explorer: schemas, tables, views, procedures, functions, columns for completion | Done |
| WH-03 | Script as SELECT, CREATE, ALTER, DROP | Done |
| WH-04, WH-05 | SQL from a template, with fill-in parameters | This release |
| WH-06, WH-07 | Saved and shared queries, history and Fabric query insights | Planned |
| WH-08 | Messages and a quick chart (several result sets come with scripts) | Done |

Later stages (building and deploying objects from Git, loading and managing data, Snowflake) follow the same pattern: a mockup first, then one change per feature.

# Warehouses (Preview)

KratosForge is building a full development experience for Fabric warehouses, then Snowflake: query, explore, template, save, build and deploy. This page covers what ships today and grows with each stage. The program's plan and order are in the [warehouse proposal](#the-plan).

## The Warehouse page

In **Workspaces**, open a workspace and press **SQL query** on a **Warehouse** or **SQL analytics endpoint** row. The Warehouse page opens: the SQL editor, the compatibility check and the managed results you know from lakehouse queries, with the warehouse as the target.

- **It runs in Fabric and uses capacity.** Unlike a lakehouse query, which runs on this PC, a warehouse query runs on the warehouse. The first run on each warehouse after the app starts asks you to confirm.
- **It only reads.** One statement that reads is sent. DDL, DML, `EXEC`, variables and anything that reaches outside the warehouse (`OPENROWSET`) are refused before Fabric is asked anything. Changing a warehouse from the app is a later stage and waits on its own decision.
- **It runs as you.** The app signs in to Azure SQL (`https://database.windows.net`) with your account, the first time you run a warehouse query, and Fabric's permissions on the warehouse apply as they do in Fabric. See [permissions](permissions.md).
- **Same run controls.** Stop, the time limit in Settings › SQL, results a page at a time, Copy and Export to CSV or Parquet. The `sql_execution` audit line names the workspace and warehouse, never the SQL.
- **Check before you run.** The editor checks T-SQL against Fabric Warehouse (or the SQL analytics endpoint) as you type, from Microsoft's [T-SQL surface area](https://learn.microsoft.com/en-us/fabric/data-warehouse/tsql-surface-area) page. See [SQL compatibility](sql-compatibility.md).

A SQL analytics endpoint is found through its lakehouse, which Fabric gives the same name in the same workspace. If the lakehouse isn't in the list, the row has no **SQL query** button; use **Open in Fabric**.

The page is tagged Preview until a query has been run on a tenant. Settings can hide preview features.

How a run works underneath (states, limits, what is refused): [Running SQL queries](sql-execution.md#queries-on-a-warehouse-preview).

## The plan

| Stage | What it adds | State |
| --- | --- | --- |
| WH-01 | The Warehouse page and its warehouse target | This release |
| WH-02 | Object explorer: schemas, tables, views, procedures, functions, columns for completion | Next |
| WH-03 | Script as SELECT, CREATE, ALTER, DROP | Planned |
| WH-04, WH-05 | SQL from a template, with fill-in parameters | Planned |
| WH-06, WH-07 | Saved and shared queries, history and Fabric query insights | Planned |
| WH-08 | Messages, several result sets and a quick chart | Planned |

Later stages (building and deploying objects from Git, loading and managing data, Snowflake) follow the same pattern: a mockup first, then one change per feature.

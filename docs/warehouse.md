# Warehouses (Preview)

KratosForge is building a full development experience for Fabric warehouses, then Snowflake: query, explore, template, save, build and deploy. This page covers what ships today and grows with each stage. The program's plan and order are in the [warehouse proposal](#the-plan).

## The Warehouse page

In **Workspaces**, open a workspace and press **SQL query** on a **Warehouse** or **SQL analytics endpoint** row. The Warehouse page opens: the SQL editor, the compatibility check and the managed results you know from lakehouse queries, with the warehouse as the target.

- **It runs in Fabric and uses capacity.** Unlike a lakehouse query, which runs on this PC, a warehouse query runs on the warehouse. The first run on each warehouse after the app starts asks you to confirm.
- **It reads until you allow changes.** Until **Allow changes** is ticked, one statement that reads is sent; DDL, DML, `EXEC`, variables and anything that reaches outside the warehouse (`OPENROWSET`) are refused before Fabric is asked anything. With it ticked, a script may change the warehouse after it shows what it changes and you confirm: see [Changing the warehouse](#changing-the-warehouse).
- **It runs as you.** The app signs in to Azure SQL (`https://database.windows.net`) with your account, the first time you run a warehouse query, and Fabric's permissions on the warehouse apply as they do in Fabric. See [permissions](permissions.md).
- **Same run controls.** Stop, the time limit in Settings › SQL, results a page at a time, Copy and Export to CSV or Parquet. The `sql_execution` audit line names the workspace and warehouse, never the SQL.
- **No risk check, by design.** The [risk check](finops/risk-check.md) looks at workloads (notebooks, pipelines, Data mesh runs) before a run. A warehouse query isn't a workload: a change shows what it changes before it runs, and the confirm, Stop and the time limit bound what it can use. Query insights on the History tab show what queries cost in time; capacity cost comes later, in Optimize.
- **Check before you run.** The editor checks T-SQL against Fabric Warehouse (or the SQL analytics endpoint) as you type, from Microsoft's [T-SQL surface area](https://learn.microsoft.com/en-us/fabric/data-warehouse/tsql-surface-area) page. See [SQL compatibility](sql-compatibility.md).

## Changing the warehouse

Tick **Allow changes** at the top of the Warehouse page to let **Run** change that warehouse: DDL, DML and `EXEC`. It's ticked per warehouse and only until the app closes, so each page starts by reading.

1. **Run** first asks the engine what the script does. Nothing is sent to Fabric for this. A script that only reads runs as before.
2. A script that changes the warehouse shows a confirm: the workspace, the warehouse, its environment, and each statement that changes something, such as *Statement 1: UPDATE dbo.DimCustomer changes the warehouse*.
3. In **Prod**, or in a workspace whose environment isn't known, you type the warehouse's name before **Run in Prod** is enabled. The engine checks that name against Fabric's, so a change never reaches a warehouse you didn't name. Dev and Test need only the confirm.
4. The script runs in one transaction, in batches split at `GO` lines. If a statement fails, or you press **Stop**, nothing is kept. **Messages** and the **History** tab show how many rows changed and that it was committed, and the explorer lists the objects again.

Some things are never run, even when changes are allowed: `BEGIN TRAN`, `COMMIT` and `ROLLBACK` (the run is the transaction), dynamic SQL, `EXECUTE AS`, permissions (`GRANT`, `REVOKE`, `DENY`, roles and users, which are managed in Fabric), `USE` and other databases, `COPY INTO` and database DDL. A script with one of these says which statement and why, and nothing is sent. A procedure's body is checked when you create it here, but a procedure already in the warehouse runs as it was written. The full list is in [SQL execution](sql-execution.md#changes-on-a-warehouse-wh-10).

**Settings › Advanced › SQL queries › Warehouse changes** turns changes off for this PC, which leaves **Allow changes** greyed out; your organization can turn them off for everyone. The audit log records who changed which warehouse and how many rows, never the SQL or the data.

## The project in Git

A warehouse's schema can live in Git the way Fabric's own Git integration keeps it: a SQL database project in a `<warehouse>.Warehouse` folder, with `.platform`, `<warehouse>.sqlproj` and one `.sql` file per object. The **Project** tab beside **History** works with that folder in any repository you opened in Source control.

```
WH_Gold.Warehouse/
    .platform
    WH_Gold.sqlproj
    dbo/Tables/DimCustomer.sql
    dbo/Views/vw_SalesBySegment.sql
    dbo/StoredProcedures/usp_RefreshSegments.sql
    dbo/Functions/fn_FiscalYear.sql
    Security/staging.sql
```

- **Finding it.** The tab looks for a folder named after the warehouse, or whose `.platform` names it, up to four folders deep in each repository. With several, a list picks one. The header shows the folder, the repository and the branch, and each file new or changed since the last commit is marked **A** or **M**.
- **Import from the warehouse** scripts every table, view, procedure, function and schema from the warehouse's catalog (catalog queries in Fabric, so it uses a little capacity and asks first, as the explorer does) and shows what it would do: new files, files that differ from the warehouse, and files already the same. New files are written. A file that differs is kept unless you tick **Replace**. No file is ever removed, so an object that's only in the project stays. With no project yet, Import makes `<warehouse>.Warehouse` in the repository you pick, with its `.platform` and `.sqlproj`.
- **Each object goes to the file that already defines it**, found by the file's first `CREATE`, so a project laid out another way (for example `Stored Procedures` with a space) is read and written the same way. New files go where the project already keeps that kind of object, or else in the layout above.
- **A table is scripted from its columns**: names, T-SQL types, and NULL or NOT NULL. Views, procedures and functions come from `sys.sql_modules` as written.
- **Open a file** to edit it in its own tab, with the same checks and completion as a query. **Save file** (or Ctrl+S) writes it back. A file changed since you opened it, for example by a Git pull or VS Code, isn't overwritten.
- **New object** writes a short starting script for a table, view, procedure, function or schema where the layout puts it, and opens it.
- **Nothing is committed and nothing runs.** Files change on this PC. Review and commit them in Source control, and Fabric's Git integration reads them when the workspace syncs. Deploy comes next (WH-13).
- A SQL analytics endpoint has no Project tab: it isn't kept in Git, its lakehouse is.

### Compare the project with the warehouse

**Compare with WH_Gold** on the Project tab reads the warehouse's catalog (catalog queries in Fabric, as Import does) and opens a **Compare** tab beside your queries. Nothing changes on either side.

- Each object is **Changed**, **Only in project**, **Only in warehouse** or **Same**, with a count for each; pick one to see only those. Changed objects come first.
- Two scripts are the **same** when they read the same as T-SQL: comments, `GO` lines, semicolons, spacing, the letter case of keywords, and brackets around plain names don't count. Names keep their case, since a warehouse's default `Latin1_General_100_BIN2_UTF8` collation makes `dbo.Sales` and `dbo.sales` two tables. Strings are compared as written, and a `GO` line inside a string or comment stays part of it.
- Pick an object to see the warehouse's script beside the project's file, line by line. For a table, the columns that differ are listed above it, such as *column Segment: varchar(20) NULL in the warehouse, varchar(40) NULL in the project*. A table in the project with constraints the warehouse's catalog script doesn't carry shows as *the table's options differ*.
- **Open file** opens the project's file in a tab. **Take the warehouse's version** writes the warehouse's script into the project (asking first when it replaces the object's file, and never writing over a file that holds another script); commit it in Source control.
- A file that doesn't create an object, or defines one already defined in another file, is listed as not compared.
- **Compare again** reads the catalog again. Closing the Compare tab forgets the comparison.

## Explorer

The left side lists the warehouse's schemas, and in each its **Tables**, **Views**, **Stored procedures** and **Functions**, read from the warehouse's own catalog views (`sys.objects`, `sys.columns`, `sys.types`). Listing runs two catalog queries in Fabric, so it uses a little capacity: it starts by itself once you have confirmed a run on that warehouse this session, otherwise **List objects** asks first.

- Click a table or view to run `SELECT TOP (100) *` on it (in the current tab when it's empty, else a new one).
- Open a table or view to see its columns with their T-SQL types, such as `decimal(18,2)` or `nvarchar(100)`. Click a column, procedure or function to type its quoted name in the editor.
- **Script as** (an object's **⋯** button, or right-click): **Select top 100 rows**, **Script as CREATE**, **Script as ALTER** (views, procedures and functions) and **Script as DROP**. The script opens in a new tab and never runs: this page runs only queries that read. A table's CREATE is built from the columns already listed, so it asks Fabric nothing. A view, procedure or function's definition is read from `sys.sql_modules` (one catalog query in Fabric), and ALTER turns its leading `CREATE` or `CREATE OR ALTER` into `ALTER`. A table's CREATE carries column names, types and NULL or NOT NULL only, not constraints.
- **Filter objects** narrows the tree by name or schema. **Refresh** lists them again.
- The editor completes table, view and column names from the same list, without asking Fabric again.

A SQL analytics endpoint is found through its lakehouse, which Fabric gives the same name in the same workspace. If the lakehouse isn't in the list, the row has no **SQL query** button; use **Open in Fabric**.

## SQL from a template

**SQL from a template**, under the arrow beside **New SQL query** on the Warehouse page, opens a gallery of T-SQL for Fabric Warehouse, in the groups Fabric's own *New SQL query from template* uses plus accelerators:

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

### Your own templates

**New template** in the gallery writes a template of your own: a title, an optional group and description, the SQL with fields marked as `<name, kind, default>` (for example `[<table_name, sysname, FactSales>]`; the kinds are `sysname`, `columns`, `string`, `int`, `datatype` and `datetime`), and whether to offer it on SQL analytics endpoints too. It starts from the current tab's SQL. As you type, the page lists the fields it found, says which kind it doesn't know, and runs the compatibility check on the template filled with its defaults. **Make a copy** on any built-in template starts one of yours from it.

Your templates show in the gallery beside the built-in ones, in their group (or **My templates**), with the same fields, checks and preview. **Edit**, **Export** and **Delete** (which asks first) are on each of them.

Each template is a `.sql` file you can read and edit anywhere: a few header lines, a blank line, then the SQL. The blank line ends the header, so comments at the top of your SQL stay part of it.

```sql
-- template: Top rows
-- group: Team checks
-- description: The first rows of a table
-- endpoint: yes

SELECT TOP (<n, int, 10>) * FROM [<schema_name, sysname, dbo>].[<table_name, sysname, FactSales>];
```

- **Save to My templates** keeps it on this PC, in the `sql-templates` folder of the app's data folder.
- **Save to a Git repository** you opened in Source control writes it to that repository's `sql-templates` folder. Commit and push it in Source control, and everyone who opens the repository gets it in their gallery. The app writes the file only; it doesn't commit.
- **Export** writes a copy to your Downloads folder (never replacing a file), to send to someone. **Import** adds a `.sql` file you pick to My templates. A file without header lines is imported with its file name as the title.
- A file the gallery can't use (too large, not UTF-8 text, or a field with an unknown kind) is still listed, with why, so you can fix it.

## Saved and shared queries

**Save query** on the Warehouse page keeps the current tab as a `.sql` file. Give it a name, an optional description, and where to keep it:

- **My queries**, on this PC: the `sql-queries` folder in the app's data folder.
- **A Git repository** you opened in Source control: its `sql-queries` folder. Commit and push it in Source control, and everyone who opens the repository sees it. The app writes the file only; it doesn't commit.

The explorer's **Queries** tab lists them, My queries first, then each repository's shared ones, with a filter. Click one to open it in a new tab (or in the tab that already holds it). A query saved from another warehouse says which; it opens here all the same. A tab opened from a file remembers it, so **Save query** writes back to that file without asking (keeping the warehouse it was saved from), and **Save as new** keeps a copy under another name. **×** on a query deletes its file after asking; tabs that hold it keep their SQL. Saving or deleting runs nothing in Fabric.

A saved query is plain T-SQL with a few header lines, so any `.sql` file put in one of those folders shows up too, named after its file. A blank line ends the header:

```sql
-- query: Revenue by region
-- warehouse: WH_Gold
-- description: Monthly review

SELECT Region, SUM(Amount) AS Revenue FROM dbo.FactSales GROUP BY Region;
```

If the file changed after you opened it (a Git pull, or another editor), **Save query** leaves it alone and says so: open it again to see the change, or use **Save as new** to keep yours. The same rules as your own templates keep these files in their place: a folder or file that is a link to somewhere else is skipped, and each place lists up to 200 queries by name, saying so when there are more.

## History and query insights

The explorer's **History** tab has two parts.

**This PC's runs** lists every query you ran on this warehouse from the app, newest first: its first line of SQL (skipping comments), when it ran, how it ended (Succeeded, Failed, Stopped, Timed out), how long it took and how many rows it returned. Hover a failed run to see why: the first line of its error, with quoted text and numbers taken out, since an error can echo values from your data. Click one to open its SQL in a new tab; it doesn't run again until you press **Run**. **Filter by text or table** narrows the list. The SQL is kept in the app's storage on this PC only (up to 200 runs per warehouse, 20,000 characters of SQL each, and about 1 MB across all warehouses: past that, or when the app's storage is full, the oldest runs go first, so open tabs always keep their room), never in the engine's logs or the audit trail. **Clear history** removes them after asking; saved queries and open tabs stay.

**Query insights from Fabric** reads Fabric's own record of everyone's queries on the warehouse, from its [queryinsights views](https://learn.microsoft.com/en-us/fabric/data-warehouse/query-insights):

| Button | What it runs |
| --- | --- |
| **Long running** | `SELECT TOP (50) * FROM queryinsights.long_running_queries ORDER BY median_total_elapsed_time_ms DESC` |
| **Frequently run** | `SELECT TOP (50) * FROM queryinsights.frequently_run_queries ORDER BY number_of_runs DESC` |
| **Recent requests** | `SELECT TOP (100) * FROM queryinsights.exec_requests_history ORDER BY start_time DESC` |

Each opens in a new tab and runs there, so it asks first like any other run and **uses capacity**. The result is an ordinary result: page through it, chart it, export it. Fabric keeps 30 days of history and fills these views a few minutes after a query ends. What you see depends on your permissions on the warehouse.

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
| WH-04, WH-05 | SQL from a template, with fill-in parameters | Done |
| WH-04b | Your own templates, on this PC or shared through Git | Done |
| WH-06 | Saved and shared queries, on this PC or through Git | Done |
| WH-07 | Query history and Fabric query insights | Done |
| WH-10 | Changes to a warehouse (DDL, DML, EXEC) after a preview and confirm, in every environment; Prod asks you to type the warehouse's name | This release |
| WH-08 | Messages and a quick chart (several result sets come with scripts) | Done |
| WH-11 | The warehouse as files in Fabric's Git layout: Import, edit, New object | Done |
| WH-12 | Compare the project with the warehouse, object by object | This release |

Next: deploy with a preview (WH-13) and the estimated plan (WH-09). Later stages (loading and managing data, Snowflake) follow the same pattern: a mockup first, then one change per feature.

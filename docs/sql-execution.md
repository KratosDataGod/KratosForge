# Running SQL queries on this PC

*KratosForge* runs every query on the SQL query page as a managed run: it gets an id, it can be stopped, it has a time limit and a memory limit, and its result is read a page at a time. This page explains what happens when you press **Run**, what a query may and may not do, and the limits you (or your IT team) can set.

A query that runs here runs in DuckDB on this PC, not in Fabric. It is not proof that the same SQL runs, or gives the same answer, in a Fabric Warehouse, SQL analytics endpoint or Spark session. See [SQL compatibility check](sql-compatibility.md) for how the page tells you about known differences. Nothing on the SQL page uses Fabric capacity.

## What happens when you press Run

1. The query gets an id and is **Queued**. If fewer than the allowed number of queries are running (2 by default), it starts at once.
2. **Running**: the query runs in a Python process of its own, with DuckDB's memory limit, a temp folder for spilled data, and external access turned off (see [What a query may do](#what-a-query-may-do)). The Results band shows how long it has run and how many rows it has written so far.
3. The result is streamed, batch by batch, into a snapshot file in the temp folder. The page shows the first page of rows (1,000 by default). **Next ›** and **‹ Previous** read other pages from the snapshot; the query doesn't run again, and rows are never counted with `COUNT(*)`: the total is the number of rows written.
4. The run ends in one of these states:

| State | What it means |
| --- | --- |
| **Succeeded** | The query finished. Its rows can be paged and exported. |
| **Failed** | The SQL had an error, or a statement wasn't allowed. The reason is shown in place of the rows. |
| **Stopped** | You pressed **Stop**. |
| **Timed out** | It ran past the time limit (10 minutes by default, or your organization's limit if it sets one) and was stopped. The message says which limit stopped it. |

**A result is never shorter than the query's without saying so.** A run that Stop, the time limit or its process ending cuts off part way keeps none of the rows it read, and says why in place of them; it never shows the first part as the result. The page, sorting, filtering, column statistics, Compare and every export read the whole snapshot, and a snapshot that holds fewer rows than the run wrote (a damaged or half-copied file) is never shown or exported as if it were whole: the page says to run the query again, and an export stops with both counts and removes its file. **Copy ▾** copies the page on screen, and when the result has more rows its message says how many and to use **Export** to save them all. Sorting, filtering and statistics on a result of more than 5,000,000 rows, and Excel on one of more than 1,048,575, are refused with the result's row count.

**Stop** ends that query's own process and nothing else: no notebook kernel, no other query, no other part of the app. A query that was still queued simply never starts. If the app closes, its queries stop with it.

Column names that repeat (`SELECT id, id FROM ...`), `NULL`s and column types are kept as the query returned them.

### Sort and filter the result

Click a column's header to sort the whole result by it: once for ascending, again for descending, a third time for the order the query returned. Empty values go last. **Filter** shows a box under each header; what you type applies to every row of the result, and the band says how many match, as "3 of 5 rows (filtered) · sorted by orders". Sorting and filtering read the snapshot on this PC; the query doesn't run again. They work on results of up to 5,000,000 rows.

| In a filter box | Keeps rows where the column |
| --- | --- |
| `West` | contains it, ignoring case (on a number column, equals it) |
| `= West`, `!= Central`, `<> Central` | equals it or not, ignoring case for text |
| `> 3000`, `>= 3000`, `< 3000`, `<= 3000` | compares as a number, a date (`2026-10-01`), a time (`14:30`) or text, by the column's type |
| `= true`, `!= false` | on a true/false column |
| `null`, `not null` | is empty or not |

`%` and `_` mean themselves, not wildcards. A filter that can't be read for its column, such as `> lots` on a number column, is marked, and its message says what to write; the rows on screen stay until it's fixed. Turning **Filter** off clears the filters.

### Select, copy and look closer

Click a cell to select it; Shift+click or drag selects a block, a row's number selects the row, and Ctrl+A selects the page. The arrow keys move (Shift and an arrow extends), and the band says how many cells are selected, as "3 × 2 cells selected".

| Key or menu item | What it does |
| --- | --- |
| **Copy** (Ctrl+C) | The selected values, tab-separated, as a spreadsheet pastes them. A value with a tab or a line break stays one cell, and text that starts with `=`, `+`, `-`, `@`, a tab or a carriage return (after any spaces) gets a leading `'` so a spreadsheet never runs it as a formula, even for one cell. **Copy** in **Open value** gives a value exactly as it is |
| **Copy with headers** (Ctrl+Shift+C) | The same, with the column names first |
| **Copy as CSV**, **JSON** or **INSERT statements** | The selection in that format, as **Copy ▾** does for the page |
| **Open value** (Enter, or double-click) | The value in full beside the grid: JSON and long text formatted, with its type and length. Esc closes it |
| **Column statistics** | Rows, empty values, distinct values (approximate), and for numbers min, max, mean and a histogram; for dates the range; for text the most common values |

Right-click a cell, or press Shift+F10, for the menu. Column statistics are over every row of the result's snapshot on this PC, not just the page, and ignore the filters; nothing runs again.

### Arrange the columns

Drag the edge of a column's header to resize it, or focus the edge and use the left and right arrow keys; double-click the edge to fit the column again. **Columns...** shows, hides and reorders the columns. A hidden column isn't shown or copied, but sort, filters and Export still cover every column. The layout stays for the tab's next run while the query returns the same column names; **Show all** puts every column back, in order and at its own width.

## Export

**Export ▾** saves the whole result, not just the page you see, to your Downloads folder:

- **CSV**, with a header row. `NULL`s are written as empty fields and empty text as `""`, so the two stay apart. Decimals keep every digit, times keep their microseconds, and binary values are hex, as T-SQL shows them (`0x00FF`). A text cell or a column name that starts with `=`, `+`, `-`, `@`, a tab or a carriage return gets an apostrophe in front (`'=1+2`), so Excel, LibreOffice or Google Sheets shows it as text instead of running it as a formula; numbers, dates and every other non-text value are written as they are (`-5` in a number column stays `-5`). When cells were protected, the message after the export says how many.
- **CSV, Keep raw values**: the same CSV with every text value exactly as it is, for loading into another tool. Only open it in a spreadsheet if you trust the data. The choice is for that one export; the next CSV is protected again.
- **Parquet**, with the result's column types. Parquet files can't hold two columns with the same name, so a repeated name gets a number: the second `id` becomes `id_2`.
- **Excel** (.xlsx), one sheet with a bold header row that stays in view, up to 1,048,575 rows (a sheet's limit; a bigger result is refused before anything is written). Values that look like formulas (`=...`) are saved as text, so opening the file never runs them. What Excel can't hold is saved as text with every digit: a number of more than 15 digits (a `decimal(38, 6)` or a 19-digit ID), a time with digits below the millisecond, and a date before 1900. Times with milliseconds show them. Binary values are hex. A text longer than 32,767 characters is cut there, and control characters are left out.
- **JSON**, an array with one object per row, keyed by column name (a repeated name gets a number, as in Parquet). `NULL`s and values that aren't numbers (NaN, infinity) are `null`, dates and times are ISO 8601 text, decimals are text so no digit is lost, and binary values are base64. Whole numbers stay numbers; one larger than 9,007,199,254,740,991 (a 64-bit ID, say) is written exactly, but JavaScript and Power Query read it rounded, so cast such IDs to text in the query (`CAST(id AS STRING)`) when another tool reads the file.

**What an export couldn't keep exactly is said with it.** The message that names the saved file also says, column by column, what the format couldn't hold as it is: "order_id: 4 numbers have more than 15 digits, which Excel can't hold, so they're saved as text with every digit.", or that Excel has no time zones. It names the first three, then how many more there are. A warehouse result says two more things in every format, since a result keeps times to the microsecond: the seventh digit of a `datetime2(7)`, `time(7)` or `datetimeoffset(7)` isn't kept, and a `datetimeoffset` is given in UTC without the offset it was written with. To keep either, convert the column to text in the query, such as `CAST(col AS VARCHAR(34))`. Parquet keeps every value with its type.

The export streams from the snapshot, so a large result is never loaded whole. The band shows its progress, and **Cancel** stops it. The file is written as `<name>.partial` and renamed when it's complete, so you never see half a file; a stopped or failed export leaves nothing behind. An existing file is never replaced: a name that is taken gets `(2)`, `(3)` and so on.

A result is kept for 24 hours by default (see [Limits](#limits)). If it has been cleaned up, **Export** asks before running the query again, because the tables may have changed since.

## What a query may do

Before anything runs, every statement in the query is put in one of four classes:

| Class | Examples | On the SQL page |
| --- | --- | --- |
| **Reads tables** | `SELECT`, `WITH ... SELECT`, `UNION`, `DESCRIBE` | Runs |
| **Changes this run's own session** | `CREATE TEMP VIEW`, `CREATE TABLE t` and `INSERT INTO t` for a table made in the same query, `CACHE TABLE` | Asks first. Only that run's own DuckDB session changes; nothing in the lakehouse, on this PC or in Fabric does |
| **Would change a lakehouse table** | `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `DROP`, `ALTER`, `TRUNCATE` or `CREATE OR REPLACE` of a lakehouse table, `CREATE SCHEMA` | Never runs. Use **Save as table**, a notebook or the metadata framework, which write to OneLake with their own confirmation |
| **Would change something outside the run** | `OPTIMIZE`, `VACUUM`, `GRANT`, `COPY`, `ATTACH`, `INSTALL`, `LOAD`, `EXPORT` | Never runs. The SQL page doesn't change Fabric, files on this PC or DuckDB extensions |

If any statement is in a class that isn't allowed, nothing in the query runs, and the page says which statement and why.

On top of that, once the lakehouse's tables are opened, the run's DuckDB has external access turned off and its settings locked. So even a statement that slips past the classes can't read or write files on this PC (`read_csv('C:\...')`), load extensions, reach the network, or raise its own memory limit. The only tables a query can read are the lakehouse's tables it names.

The storage token for the lakehouse is handed to the query's process only when it first reads a table, kept in memory, and never written to a log, the audit trail or the snapshot.

## Queries in a local data project

A run can also go to a [local data project](local-data.md) instead of a lakehouse (engine call `sql_start` with `target = "local"` and the project; the screens for it come with the local data pages). It is the same managed run: the same states, **Stop**, time limit, pages and export. The differences:

- It runs in the project's own database, inside the app's engine rather than in a process of its own, because only one program can open a project's database for writing and the engine already has it open. **Stop** and the time limit interrupt the query in that database; nothing else in the project or the app is stopped. The memory limit is the project's own setting. The time limit is the SQL page's; when that is off (0), the project's own time limit applies, and the run's status says which (`time_limit_from`: `sql`, `project`, `policy`, or `caller` for a limit the engine call passed). A time limit your organization's policy sets is a ceiling: an engine call can ask for a shorter one, but not a longer one or none. A stopped query frees what it held when it stops, including its memory and the data it spilled to the project's `tmp` folder, and the next query in the project runs as usual.
- It reads the project's tables, and it may change them: `CREATE TABLE`, `INSERT`, `UPDATE` and `DELETE` with a `WHERE`, `ALTER`, and the project's [Delta table](local-data.md#delta-tables) statements run without asking. The class is **Changes the project's own tables** (`local_project_write`).
- A statement that removes or replaces a table or every row asks first: `DROP`, `TRUNCATE`, `CREATE OR REPLACE`, `INSERT OVERWRITE`, and `DELETE` or `UPDATE` without a `WHERE`. The run fails with nothing run and says which statement; run it again with **confirm** (`confirm = true`) to let it go ahead. A table made earlier in the same query can be dropped, replaced or emptied without asking.
- Statements that would reach outside the project never run, confirmed or not: `COPY`, `EXPORT DATABASE`, `IMPORT DATABASE`, `ATTACH`, `INSTALL`, `LOAD`; reading files or other databases (`read_csv`, `read_parquet`, `read_text`, `glob`, `FROM 'file.csv'`, Spark's ``csv.`path` `` and the like); SQL text run inside a query (`query('...')`); settings that show this PC's paths (`current_setting`, `duckdb_settings()`); and `SET`, `RESET` or `PRAGMA`, which would change the project database's settings. Add a file to the project first, then query its table.
- This is a check of each statement before anything runs, not a sandbox: unlike a lakehouse run, the project's database keeps DuckDB's file access on, because the project's own tables live in files. A quoted table name with a dot or slash in it (`"my.table"`) is refused as if it were a file.

**Dry run.** `sql_start` with `dry_run = true`, for any target, lists each statement with its class and whether it needs confirming, and says whether the run would be refused. Nothing runs, and no run is kept.

Lakehouse runs are unchanged: their writes are still refused, as in the table above.

## Queries on a warehouse (Preview)

A run can also go to a Fabric **Data warehouse** or a lakehouse's **SQL analytics endpoint** (engine call `sql_start` with `target = "warehouse"`, `dialect = "tsql"` and `warehouse_ref`: `workspace_id`, `item_id` and `kind`, `warehouse` or `endpoint` with the lakehouse's id). The [Warehouse page](warehouse.md) is its screen: **SQL query** on a Warehouse or SQL analytics endpoint row in Workspaces. Unlike every other run here, **it runs in Fabric, so it uses capacity**. It is tagged Preview until it has been tested on a tenant. It is the same managed run: the same states, **Stop**, time limit, pages and export. The differences:

- **It only reads, unless started as a change (below).** The T-SQL is read before anything is sent, and it must be one statement that only reads: `SELECT`, `WITH ... SELECT`, `UNION` and the like. A second statement, `SELECT ... INTO`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `EXEC`, `DECLARE`, transactions, `OPENROWSET`, `OPENQUERY`, `OPENDATASOURCE`, `COPY INTO` and `BULK INSERT` are refused, and nothing is sent to Fabric. T-SQL the check can't read is refused too. A read at a past time, ending in `OPTION (FOR TIMESTAMP AS OF '<UTC time>')`, is a read: the check reads the query without that hint, which only picks the version of the tables, and the query is sent as written. The run is also held in a transaction that is rolled back when it ends, so even a statement the check misread changes nothing.
- **Fabric's own server only.** The server and database are read from Fabric for the item you picked, and the server must be a `*.datawarehouse.fabric.microsoft.com` host, so the sign-in token is never sent anywhere else.
- **As you.** It connects with Microsoft's SQL driver (`mssql-python`) and a token for Azure SQL (`https://database.windows.net`), asked for the first time you run a warehouse query. Fabric's permissions on the warehouse apply as they do in Fabric.
- **A warehouse that is waking up.** When the warehouse turns the login away for a passing reason, the connect is tried again, up to 4 times within 2 minutes (waits and logins together), with a fresh token each time. That covers resuming or unavailable (40613), busy (40501), not enough resources (49918, 49919, 49920, 10928, 10929, 40197), a secondary still starting (4221) and a login timeout, read from the error's text. Nothing has been sent at that point, so a change is never sent twice. A refused login (18456, 40532) isn't tried again. Neither is anything after the connection opens. **Stop** ends a run that's waiting to try again. If every try fails, the run says so with the last error number.
- **Stop and the time limit.** The run is a process of its own, as a lakehouse run is. **Stop** and the time limit end that process, which closes the connection, and Fabric stops the query when its connection closes. The time limit is also the driver's query timeout.
- The `sql_execution` audit line names the workspace, warehouse id and warehouse, and never the SQL or the token.

### Changes on a warehouse (WH-10)

A warehouse run may also **change** the warehouse, when it's started as a change: `allow = ["warehouse_write"]`, `confirm = true` and `confirm_name`, the warehouse's name as you confirmed it. The page shows what the script changes first (a `dry_run` with the same `allow` lists each statement and needs no confirm). Then:

- **The name is checked against Fabric's.** The engine reads the warehouse's name from Fabric for the item and sends nothing unless it is the name you confirmed, so a change never reaches a warehouse you didn't name.
- **A script, in batches.** The SQL may be several statements, split into batches at `GO` lines as in SSMS: `CREATE`, `ALTER`, `DROP`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `TRUNCATE`, `EXEC`, `SELECT ... INTO`, variables and reads. The last result set is the one you page through.
- **One transaction.** Every batch runs on one connection in one transaction, committed once all of them succeed. A failed statement, **Stop** or the time limit rolls it all back, so a failed change keeps nothing. The status adds `affected` (rows changed) and `committed`.
- **A script written as one transaction** runs as the run's own transaction: `BEGIN TRAN` (or `BEGIN TRANSACTION`) on its own first line and `COMMIT` on its own last line, `GO` lines allowed between and after, are taken off before it runs, and the rest commits once every batch succeeded or keeps nothing. If a batch fails, the error names it ("Batch 2 of 3 (UPDATE dbo.sales ...) failed, so the whole change was rolled back and nothing was kept. Fix that batch and run the change again. ..."), and the run's status keeps it as `failed_batch`.
- **A run cut off part way** (a dropped connection, an expired sign-in, throttling, the warehouse cancelling it, or the time limit) ends with `error_kind` `connection`, `signin`, `throttled`, `cancelled` or `timeout` and a message that says what was kept and what to do next. A connection lost while a change was being committed is `commit_unknown`: the change may or may not have been kept. See [When a run is cut off](warehouse.md#when-a-run-is-cut-off).
- **Table clones.** `CREATE TABLE schema.clone AS CLONE OF schema.source [AT 'YYYY-MM-DDThh:mm:ss']` runs as a change, and the confirm names the clone, its source and the UTC time it is cloned as of ("as it is now" without `AT`). A clone Fabric can't make is refused before anything is sent, with why: a name with a database in it (a warehouse clones only within itself), an `AT` time that isn't written as `'YYYY-MM-DDThh:mm:ss'` with up to three decimal places for the seconds, a time later than now, or one more than 120 days ago (the longest a warehouse keeps a table's history; a warehouse whose retention is shorter refuses an older time itself). See [Table clones and warehouse snapshots](sql-compatibility.md#table-clones-and-warehouse-snapshots).
- **Still refused**, before anything is sent: any other `BEGIN TRAN`, `COMMIT` or `ROLLBACK`, a named or nested transaction and `SAVE TRAN` (the run is the transaction); dynamic SQL, `EXEC('...')`, `EXEC (@sql)`, `EXEC @name`, `EXEC @r = @name` and `sp_executesql`, since what it runs can't be checked first; `EXECUTE AS` and `REVERT` (it runs as you); `GRANT`, `REVOKE`, `DENY`, `AUTHORIZATION`, roles and users (permissions are managed in Fabric); `USE` and changes to a table in another database (open that warehouse instead); `COPY INTO` and `BULK INSERT` (loads files and isn't run from the SQL page); `CREATE`, `ALTER` or `DROP DATABASE`, including `ALTER DATABASE ... SET TIMESTAMP`, which moves a warehouse snapshot (use **Capture new state** on the snapshot in Fabric); and `OPENROWSET`, `OPENQUERY` and `OPENDATASOURCE`. These words are looked for anywhere outside comments, strings and quoted names, read left to right so a quote or `--` inside `[it's]` or `[a--b]` stays part of the name. A column or table named after one of them is written in brackets, such as `[Use]` or `[Grant]`. Procedure names are checked with their brackets or quotes taken off, so `[sys].[sp_executesql]` is caught. Procedure and function bodies are checked the same way, so a procedure that commits or grants can't be created here. A batch the check can't read is refused, unless it defines a procedure, function, view or trigger. A procedure that is already in the warehouse runs as it was written: the confirm says so.
- **Settings › Advanced › SQL queries › Warehouse changes** (`sql_warehouse_writes`, on by default) turns every change off on this PC. Your IT team can set it in the policy file; a policy file that can't be read turns changes off.
- The audit line adds `affected` and `committed`, and still never has the SQL.

### Estimated plan (WH-09)

`estimated_plan = true` on a warehouse run that reads asks Fabric for the plan instead of the rows: the engine runs `SET SHOWPLAN_XML ON`, the query, then `SET SHOWPLAN_XML OFF`, on the same connection and in the same rolled-back transaction, so the query itself doesn't run. The result is the showplan XML, and the status adds `plan: true`. It's refused for a change (`allow`) or a local data project: "An estimated plan is for a lakehouse or warehouse query that reads."

On a lakehouse (editor parity S39, S40), `estimated_plan = true` runs `EXPLAIN (FORMAT JSON)` in the run's worker after the tables are bound, so nothing runs, and `profile = true` runs `EXPLAIN ANALYZE` with DuckDB's JSON profiling, which runs the query once more. Each answers with one row of JSON in a column named `plan` or `profile`, read with `sql_page`: the plan as `{steps: [{depth, name, rows, details}], more_steps}` with estimated rows (or null), the profile as a notebook's query profile. Both are for one query that only reads: more than one statement, or one that changes even the run's own session, fails with "A plan or profile is for one query that reads", and `allow` beyond reading is refused before anything starts. Profiling is turned on before the run locks its settings, and writes to a file in the run's own folder, never to the worker's output. A profile isn't for a warehouse, and isn't an estimated plan too. The status adds `plan: true` or `profile: true`, and so does the audit line.

## Several statements in one run (Preview)

With Preview features shown, **Run** on a script of several statements runs them one after another and gives each its own result tab above the grid: `1 SELECT · 5 rows`, `2 UPDATE · 14 rows changed`, and so on. Statements are split on `;` and, for T-SQL, on `GO` lines; a semicolon or a `GO` line inside a string, a comment (comments can nest) or a quoted name (`"a;b"`, `` `a;b` ``, and `[a;b]` in T-SQL) doesn't split, nor does one inside a Spark SQL string after a backslash (`'it\'s;'`) or a DuckDB `$$...$$` string, and a T-SQL batch that declares variables or creates a procedure, function, trigger or view stays one statement. **Ctrl+Shift+Enter**, a selection, and a single statement run as before, with one result.

- On a lakehouse, the statements share one DuckDB session, so a temporary view made by the first can be read by the second. Every statement is read and checked before the first one runs: a statement that can't be read, or that [isn't allowed](#what-a-query-may-do), stops the run before anything runs, and the message names it (*Statement 2: ...*).
- On a warehouse, the statements run one at a time on one connection. A script that only reads may hold several reads. A script that changes the warehouse still goes through **Allow changes** and the confirm, and runs in one transaction: `BEGIN TRAN` and `COMMIT` around it are dropped, since the run has its own.
- The run stops at the first statement that fails. The tabs before it keep their results; the failed one shows the error, and the rest show *Not run*. A note under the tabs offers **Go to line n** and **Run from statement n**. On a warehouse, a failed change rolls back every statement before it; on a lakehouse, a temporary view or setting made before the failure stays in that session only.
- Each tab's result can be paged and exported on its own. At most 200 statements run in one go.

## Query parameters (Preview)

A query can name a value it needs as `:name` or `{{name}}`, as in `WHERE region = :region AND order_date >= {{start_date}}`. A name inside a string, a comment or a quoted name isn't a parameter, and `::` (a cast) never starts one. A backslash escapes a quote only in Spark SQL; in DuckDB `'C:\'` is a whole string. DuckDB's list slice `l[:n]` reads as the parameter `n`, so write it `l[1:n]` or `list_slice(l, 1, n)`. With Preview features shown, **Run** asks for each value and its type once: **Text**, **Number** (42 or 3.5), **Date** (2026-10-01), **Date and time** (2026-10-01 14:30) or **True or false**. A value that doesn't fit its type is pointed out, and nothing runs until it does.

The values never become part of the SQL text. On a lakehouse each is bound to DuckDB as a typed parameter; on a warehouse each is sent to the driver as a `?` parameter. So a value such as `x' OR '1'='1` is only ever that text. The values are kept with the tab (shown as a chip beside **Run**, with **Change**), so the next run doesn't ask again, and they're saved at the top of a [.sql file](#sql-files-on-this-pc-preview) as one comment line. The values are never written to the audit trail: a run with parameters is recorded with `params` noted as "(given)".

## Result history and pinned results (Preview)

The **History** view beside **Results** lists the last 20 runs of the tab: when each ran, its SQL, its parameter values, its rows and how long it took. Clicking a run's SQL shows its result again, read from its snapshot on this PC, without running the query; a note says so, with **Back to the latest**, **Pin it** and **Compare with latest**. The history lasts until the tab closes.

The engine keeps the newest 20 results of the whole page, for the retention time in [Limits](#limits). **Pin** keeps a result past both until you unpin it or close its tab, up to 10 pinned results. A result the engine no longer has says it needs running again; opening the history never runs anything.

## .sql files on this PC (Preview)

With Preview features shown, the SQL page's toolbar has **Open .sql...** and **Save as .sql...** beside **Save query**, which keeps saving to My queries or a Git repository as before. Both use the Windows file dialog, and only `.sql` files are opened or saved.

- A file opens in a tab of its own, named after the file, with *File on this PC: path* above the editor. **Ctrl+S** in that tab saves back to the file. A dot on the tab shows changes the file doesn't have yet.
- A file keeps its encoding (UTF-8, UTF-8 with a byte order mark, or UTF-16) and its line endings (CRLF or LF). A new file is UTF-8 with Windows line endings. A file that isn't UTF-8 or UTF-16 text, a file larger than 5 MB, and a link (a symbolic link or junction) to another file aren't opened.
- When another program saved the file since the tab opened it, the page says so when you come back to the app, with **Load the new version** and **Keep this tab as it is**. **Ctrl+S** never writes over such a change without asking first.
- Closing a tab with unsaved changes asks: **Save**, **Don't save** (the file stays as it was) or **Cancel**.

Saving writes the whole file at once: another program sees the old file or the new one, never half of it. Saving is recorded in the [audit trail](#the-audit-trail) with the file's path and the text noted as "(given)"; opening a file isn't recorded.

## Limits

Set them in **Settings › Advanced › SQL queries**. Your IT team can set any of them in the policy file (see [Azure setup](azure-setup.md#4-push-settings-to-everyone)); a value they set shows as *Set by your organization's policy* and can't be edited.

| Setting | Key | Default | Range |
| --- | --- | --- | --- |
| Rows per page | `sql_page_rows` | 1,000 | 100 to 10,000 |
| Time limit | `sql_time_limit_minutes` | 10 minutes | 0 (no limit) to 1,440 |
| Memory | `sql_memory_limit_percent` | 60% of this PC's RAM, held to three quarters of what is free when the query starts | 10 to 90 |
| Queries at once | `sql_max_concurrent` | 2 | 1 to 8 |
| Keep results | `sql_result_retention_hours` | 24 hours | 0 (until the app closes) to 168 |
| Temp folder | `sql_temp_dir` | `sql-runs` in the app's data folder | Any full folder path |

A query's memory is the smaller of its share and three quarters of the memory free when it starts, so on a busy PC (other apps holding most of its memory) a large query works within what is left instead of pushing the PC into paging. DuckDB gets half of that as its memory limit and spills what doesn't fit to the temp folder; the other half is for reading the lakehouse's tables, which happens outside DuckDB's limit. The query's process is held to the whole amount: past it, the query stops with a message saying how much memory was free and what to do (close other apps, or narrow the query), instead of being ended by Windows. When even spilling isn't enough within DuckDB's half, the query fails with DuckDB's own out-of-memory message. Results past their retention time, and all but the newest 20, are deleted. Results left by an earlier session are deleted the first time a query runs.

## The audit trail

Each run adds one `sql_execution` line to the [audit trail](user-guide.md#audit-trail) when it ends: its id, the target and the lakehouse or local data project, the dialect, its class, its state, the number of rows and how long it took. The SQL itself, error messages (which can quote the SQL), the token and any data are never recorded. Starting, stopping and exporting a run are recorded as calls in the usual way, with the query noted only as "(given)".

## For developers and other tools

The engine calls are `sql_start`, `sql_status`, `sql_page`, `sql_column_stats`, `sql_pin`, `sql_cancel`, `sql_export`, `sql_export_status` and `sql_export_cancel` (`sidecar/fabric_desktop/sql_exec.py`). `sql_page` takes `sort` (up to three `{column, desc}`, by position, so repeated names are fine) and `filters` (`{column, text}`, the filter box's text) and answers with the matching page, `total` (rows that match) and `all_rows`; a filter it can't read answers `filter_error: {column, message}` instead of rows. `sql_column_stats(execution, column)` profiles one column over every row. Both read the run's result file on this PC with DuckDB, every typed value as a parameter (`sidecar/fabric_desktop/sql_view.py`), and work on results of up to 5,000,000 rows. `sql_start` takes `target` (`lakehouse`, `local` or `warehouse`), `project`, `warehouse_ref`, `confirm`, `confirm_name` and `dry_run` as well, and `statements` (a list, one result each) and `params` (a list of `{name, type, value}`, see `sql_params.py`). `sql_page`, `sql_column_stats` and `sql_export` take `part`, the statement whose result to read. `sql_file_info`, `sql_file_read` and `sql_file_write` open and save `.sql` files (`sql_files.py`). `local_query_start`, `local_query_status` and `local_query_cancel` are deprecated: they forward to these runs and answer as before. `run_sql` and `save_sql`, which the page used before, still work unchanged for other callers (`export_sql_csv` is no longer an engine call; code inside the engine can still use it), as do `lakehouse.sql()` in notebooks and the read-only query that external AI tools use through the [local API](architecture/ai-integration.md).

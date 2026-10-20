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
| **Timed out** | It ran past the time limit (10 minutes by default) and was stopped. |

**Stop** ends that query's own process and nothing else: no notebook kernel, no other query, no other part of the app. A query that was still queued simply never starts. If the app closes, its queries stop with it.

Column names that repeat (`SELECT id, id FROM ...`), `NULL`s and column types are kept as the query returned them.

## Export

**Export ▾** saves the whole result, not just the page you see, to your Downloads folder:

- **CSV**, with a header row. `NULL`s are written as empty fields.
- **Parquet**, with the result's column types. Parquet files can't hold two columns with the same name, so a repeated name gets a number: the second `id` becomes `id_2`.

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

- It runs in the project's own database, inside the app's engine rather than in a process of its own, because only one program can open a project's database for writing and the engine already has it open. **Stop** and the time limit interrupt the query in that database; nothing else in the project or the app is stopped. The memory limit is the project's own setting.
- It reads the project's tables, and it may change them: `CREATE TABLE`, `INSERT`, `UPDATE` and `DELETE` with a `WHERE`, `ALTER`, `CREATE OR REPLACE`, and the project's [Delta table](local-data.md#delta-tables) statements run without asking. The class is **Changes the project's own tables** (`local_project_write`).
- A statement that removes a table or every row asks first: `DROP`, `TRUNCATE`, and `DELETE` or `UPDATE` without a `WHERE`. The run fails with nothing run and says which statement; run it again with **confirm** (`confirm = true`) to let it go ahead. A table made earlier in the same query can be dropped or emptied without asking.
- Statements that would reach outside the project still never run, confirmed or not: `COPY`, `EXPORT`, `ATTACH`, `INSTALL`, `LOAD` and the like.

**Dry run.** `sql_start` with `dry_run = true`, for either target, lists each statement with its class and whether it needs confirming, and says whether the run would be refused. Nothing runs, and no run is kept.

Lakehouse runs are unchanged: their writes are still refused, as in the table above.

## Limits

Set them in **Settings › SQL queries**. Your IT team can set any of them in the policy file (see [Azure setup](azure-setup.md#4-push-settings-to-everyone)); a value they set shows as *Set by your organization*.

| Setting | Key | Default | Range |
| --- | --- | --- | --- |
| Rows per page | `sql_page_rows` | 1,000 | 100 to 10,000 |
| Time limit | `sql_time_limit_minutes` | 10 minutes | 0 (no limit) to 1,440 |
| Memory | `sql_memory_limit_percent` | 60% of this PC's RAM | 10 to 90 |
| Queries at once | `sql_max_concurrent` | 2 | 1 to 8 |
| Keep results | `sql_result_retention_hours` | 24 hours | 0 (until the app closes) to 168 |
| Temp folder | `sql_temp_dir` | `sql-runs` in the app's data folder | Any full folder path |

When a query needs more memory than its share, DuckDB spills to the temp folder; when even that isn't enough, the query fails with a message saying so. Results past their retention time, and all but the newest 20, are deleted. Results left by an earlier session are deleted the first time a query runs.

## The audit trail

Each run adds one `sql_execution` line to the [audit trail](user-guide.md#audit-trail) when it ends: its id, the target and the lakehouse or local data project, the dialect, its class, its state, the number of rows and how long it took. The SQL itself, error messages (which can quote the SQL), the token and any data are never recorded. Starting, stopping and exporting a run are recorded as calls in the usual way, with the query noted only as "(given)".

## For developers and other tools

The engine calls are `sql_start`, `sql_status`, `sql_executions`, `sql_page`, `sql_cancel`, `sql_export`, `sql_export_status` and `sql_export_cancel` (`sidecar/fabric_desktop/sql_exec.py`). `sql_start` takes `target` (`lakehouse` or `local`), `project`, `confirm` and `dry_run` as well. `local_query_start`, `local_query_status` and `local_query_cancel` are deprecated: they forward to these runs and answer as before. `run_sql`, `export_sql_csv` and `save_sql`, which the page used before, still work unchanged for other callers, as do `lakehouse.sql()` in notebooks and the read-only query that external AI tools use through the [local API](architecture/ai-integration.md).

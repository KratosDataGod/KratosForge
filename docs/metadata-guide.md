# Metadata framework guide

The metadata framework builds and runs your medallion layers (Bronze, Silver, Gold, or any 2 to 10 layers you choose) from configuration instead of one-off notebooks. You describe each table once in a small file: where its data comes from, its columns, how to clean and check it, and how to load it. KratosForge runs every table the same way, checks it, writes it to its layer's lakehouse, and logs what happened.

This guide goes from an empty folder to Prod. The [reference](#reference) at the end lists every setting.

> **Tested and not yet tested.** Everything that runs on your PC, including Dev on the PC, the four gates and every load mode, is covered by automated tests on every build. Features that talk to Fabric (promote to Test and Prod, runs in Fabric, Optimize, the semantic model, history and restore, the variable library) are built to Fabric's documented APIs but haven't yet been tried on a live tenant. Several are tagged **Preview** in the app. Some things this guide marks Preview are settings in your project files rather than screens, such as reading **in batches** (`batch_by`), [reliability objectives](#reliability-objectives-preview) and [release records](#release-records). Turning Preview features off (`show_preview`) hides screens only, so these still run whenever a project sets them. The exception is a notebook's MERGE INTO, UPDATE and DELETE, which then run in Fabric instead of on this PC.

## Ideas in one page

| Idea | What it means |
| --- | --- |
| **Project** | A folder you keep in Git. Every table is a file in it. |
| **Layer** | A stage of your data (Bronze, Silver, Gold). Each layer is its own schema-enabled lakehouse, `LH_<Layer>`, in every environment. Layers never mix. Microsoft's medallion guidance uses one lakehouse with a schema per layer instead ([how the two differ](ai/microsoft-fabric-skills.md)). |
| **Entity** | One managed table, described by `metadata/entities/<layer>.<table>.toml`. |
| **Environment** | Dev, Test and Prod. Each has its own workspace (Dev can stay on your PC). |
| **Run** | Reading, checking and writing one or more entities in one environment. Every run goes through the same steps: read, contract, surrogate keys, clean, derive, hooks, lookups, checks, write, log. |
| **Gates** | Rules enforced at four points (save, write, promote, after) so the standards are followed without anyone policing them. |
| **Promote** | Moving a tested metadata version from Dev to Test, or Test to Prod. |
| **LH_Meta** | A lakehouse per environment that holds the run log, check results, watermarks, schema history and deployments. |
| **Keys** | Where surrogate keys are issued. Recommended: one keys database for Dev, Test and Prod, in one Keys workspace ([platform.md](platform.md#one-keys-database-for-dev-test-and-prod)). Without it, `LH_Keys`, a lakehouse per environment. |

## 1. Set up a project

1. Open **Data mesh** in the left rail. With no project yet, it shows **Set up a metadata project**.
2. **Folder:** where the project goes, as a full path (a relative path such as `crm` is refused). The default is `Documents\FabricDesktop\metadata`. Choose a folder inside a Git repository if you have one.
3. **Layers:** pick 2 (raw, curated), 3 (bronze, silver, gold) or 5 (landing, bronze, silver, gold, semantic). You can add, remove, rename and reorder later.
4. **Add the sample tables:** with 3 layers, tick this to get customers and orders from Bronze to Gold with sample CSV files, so you can try everything straight away.
5. **Create project.**

To open a project someone else made (for example, after `git clone`), use **Or open an existing project** and give the folder that has `layers.toml`.

A new project looks like this:

```
layers.toml                     your layers, in order, and each one's rules
environments.toml               Dev, Test and Prod: workspace, where it runs
exceptions.toml                 approved exceptions to a rule
metadata/entities/*.toml        one file per table
metadata/keys/*.toml            one file per business key, and source_systems.toml
transforms/                     your Python: hooks, custom checks, custom steps
tests/                          tests for that Python
.local/                         Dev's tables on this PC (in .gitignore)
```

## 2. Run Dev on your PC

A new project's Dev keeps its tables in the `.local` folder, so you can build and run everything with no Fabric workspace at all.

1. Pick **Dev** in the top bar's **Environment** picker.
2. **Run all** (top right) runs every entity in layer order. A dialog shows each table's result: rows written, rows quarantined, or why it failed.
3. **Details** on a result, or any row on the **Runs** tab, shows each step with rows and time, and each check with how many rows failed.

With the sample project, the first Run all loads customers and orders through Silver into a Type 2 customer dimension and a sales fact, quarantines a returned order whose amount isn't negative, and warns about an order whose customer doesn't exist yet.

When you're ready to use Fabric for Dev, assign it a workspace (step 7) and switch **Tables in** to **Workspace**.

## 3. Design your layers

**Data mesh > Design > Layers** lists your layers in order, with each one's rules. Change them, then **Save layers**.

| Column | What it sets |
| --- | --- |
| **Layer** | The name. The lakehouse is `LH_<Name>` in every environment. |
| **Can read from** | **Any earlier layer**, or **Only** one earlier layer. The first layer reads files and sources. |
| **Schema drift** | What happens when a source has a column the contract doesn't: **Keep new columns**, **Ignore new columns**, or **Stop on new columns**. An entity can override this. |
| **Required checks** | **Key not null** and **Key unique**: every table in the layer must have these checks on its key. |
| **Surrogate keys: Required** | Every table in the layer must take its surrogate keys from the environment's key tables ([section 8](#8-surrogate-keys)). |
| **Keep history** | How many days back a table can be restored (at least 7). VACUUM keeps files this long. |
| **↑ ↓** and **Remove** | Reorder, or remove a layer with no entities. |

**Add layer** adds one at the end (up to 10). Reordering is refused if a table would end up reading a later layer.

Rules that always hold: a table writes only to its own layer's lakehouse; it reads from earlier layers, or from its own layer when that makes no loop (a fact reading its dimensions); reading a later layer is always rejected.

## 4. Add entities

**New entity** (on any Data mesh page, or Ctrl+K) asks for:

| Field | What to put |
| --- | --- |
| **Template** | What kind of table (below). The file it writes already meets your layer's standards. |
| **Layer** and **Table name** | Together they make the entity name, such as `silver.orders`. |
| **Source** | For files: a pattern under the layer's lakehouse `Files` area, like `landing/orders/*.csv`. Otherwise another entity, like `bronze.orders`. |
| **Key columns** | The business key, comma separated. |

| Template | Makes |
| --- | --- |
| Ingest files | Reads CSV, Parquet or JSON as they arrived. Columns come from the first matching file in Dev, if there is one. |
| From the landing zone | Bronze from the raw files your central team lands in the platform's `LH_Landing`. Pick a source and entity; the source becomes `[source] landing = "<source>/<entity>/*.csv"` and columns come from Dev's newest file. See [One landing zone](platform.md#one-landing-zone). |
| Clean | Cleans, deduplicates and merges changed rows from another table. Incremental when the source has a `modified_at`-style column. |
| Type 1 dimension | Overwrites changes, with integer surrogate keys that are never renumbered. |
| Type 2 dimension | Keeps history with valid from and to dates. |
| Fact | Looks up each dimension's surrogate key as of the fact's date; unknowns get -1. |
| Custom Python step | Creates `transforms/<table>.py` and a test. You write the transform; the framework checks and writes the result. |

The entity page opens. From there:

- **What happens on every run** shows the steps for this table. Grey steps are the framework; purple is your code, at a fixed point.
- **Config** is the TOML file. Edit and **Save** (or **Ctrl+S**). Saving runs the save gate: problems show above the editor and the table can't run or be promoted until they're fixed.
- **Contract** lists the output columns and marks keys, surrogate keys, derived columns and columns your hooks add.
- **Lineage** shows what it **Reads**, the table itself, and what it **Feeds**. Click a table to open it.
- **Recent runs** shows its last runs in every environment.
- **Data in <environment>** shows a sample of the table as written.
- **Run in Dev** (or the picked environment) saves and runs just this table. **Rebuild with downstream** reprocesses it in full and then every table that reads it, in order.
- **Delete** removes the file. The Delta table stays in the lakehouse until you delete it there.

### Where custom logic goes

Simplest first. The framework still runs every other step, checks the result and writes it.

1. **An expression:** `[[derive]]` adds a column from a SQL expression; `[[check]]` with `expr` is a row-level rule.
2. **A hook:** `[hooks] after_clean` or `before_write` names a function `(df, ctx)` that returns rows. Declare the tables it `reads` and the columns it `adds`.
3. **A custom check:** `[[check]] python = "transforms.checks:valid_email"` returns true for each good row.
4. **A custom step:** `[step] custom = "transforms.sales:build"` replaces this table's transform.

Every hook, custom check and custom step needs an `owner` and a `test`, or the save gate stops it. Shared helpers go in `transforms/common.py`.

## 5. Load modes and decisions

| Mode | `[load] mode` | What a run does |
| --- | --- | --- |
| Full reload | `full` | Replaces the table with what the source has now. |
| Incremental | `incremental` | Reads only rows whose `watermark` column is past the last run's, and merges them on the key. Only the table's files that hold changed rows are rewritten (see [Large tables](#large-tables)). |
| Type 1 dimension | `scd1` | Merges on the key, overwriting changes. Surrogate keys are issued once and never renumbered; new rows get theirs in business-key order, so the same input gives the same keys on every run. An Unknown member is at -1. |
| Type 2 dimension | `scd2` | A change in a `tracked` column closes the current row and adds a new version with `valid_from`, `valid_to` and `is_current`. Columns in `type1` are overwritten on every version. New versions get their surrogate keys in business-key order, as Type 1 rows do. |

**Key tables in LH_Keys** are read only as far as a run needs. A run with up to 50,000 business keys reads just their rows, skipping the files whose range of business keys can't hold one. The next key comes from the Delta log's statistics. A run with more business keys reads the key table once, which is quicker than filtering by that many values; a run of 1 million keys against a 2 million key table takes about as long as reading the table. Keys from the platform (SQL_Platform or SQL_Keys) are issued per run in SQL.

Facts use `[[lookup]]` to take a dimension's surrogate key as it was on the fact's date. A fact row whose dimension member doesn't exist gets -1.

### Large tables

An incremental run is built to stay small when the table is big:

- **It reads only what it needs.** The watermark filter goes into the read itself, so files whose rows are all older are skipped without being opened. A table that reads another entity reads only the columns it uses (plus any new ones, so drift is still noticed). The run's **read** step says so, for example *incremental, modified after 2026-10-01; read 3 columns of 4*.
- **It rewrites only what changed.** New and changed rows go into the table with a Delta MERGE on the key: rows with a key already in the table are replaced, the rest are added, and Parquet files without any of those keys stay as they are. A table without keys gets a plain append. The **write** step says what happened, for example *merged 2000 rows on id: 1000 updated, 1000 new (Delta MERGE rewrote 1 file, added 1)*.
- **It falls back to a rewrite when a MERGE would change the result.** If the new rows repeat a key (there's no `unique` check), or a column's type changed, the run reads the whole table and replaces it, as every run did before 0.20, and the write step says why.

Replayed quarantine rows go in the same way.

**Dimensions** (`scd1`, `scd2`) read only the rows of the business keys that arrived, plus the Unknown member, merge them exactly as before (same keys, versions and Type 2 checks) and write them back with one MERGE on the surrogate key. A Type 2 dimension fed a full snapshot of its source first drops the rows that would change nothing (same tracked values, no new Type 1 value), so only changed and new keys are touched; when none changed, nothing is written at all. A Type 1 dimension takes every row that arrives, so a full snapshot rewrites it, and the write step says *most of it changed*. The write step says what it read, for example *1000 new, 1000 changed (new versions), 0 Type 1 updates, 4999000 rows unchanged; read 1001 rows of the table for 2000 business keys*.

Columns are written in the types Fabric reads, as a Local data project writes them. An unsigned integer (a Polars count such as `len()` is one) becomes the next larger signed type, and float16 becomes float. A TIME, an INTERVAL, or a column that is NULL in every row (with no type) stops the load before anything is written, with a message naming the table and column and saying how to cast it. When rows are appended, a column that is NULL in every row takes the table's type.

### Late rows and source deletes (Preview)

These are settings in your entity files, not a screen, so **Settings > Preview features** has nothing to hide for them.

By default an incremental run reads rows whose watermark is strictly after the last run's, and never looks at rows it loaded before. That misses two things: a row that arrives late, with a watermark older than the last run's, and a row deleted at the source. Two `[load]` settings, both off by default, cover them for an incremental load of a Delta source (`table`, `link` or `entity`) with `[contract] keys`:

```toml
[load]
mode = "incremental"
watermark = "modified_at"
lookback = "2 hours"     # read again the rows up to 2 hours before the watermark
deletes = "soft"         # none (default) | soft | hard
```

- **`lookback`** moves the read back from the watermark: the run reads rows after *watermark less lookback*. The rows it had already loaded come again and replace themselves through the MERGE on the key, so a late row inside the lookback lands exactly once and nothing lands twice. Use a duration (`"30 minutes"`, `"2 hours"`, `"3 days"`) for a date or datetime watermark (a date watermark goes back whole days, rounded up, so `"2 hours"` reads the watermark's own day again), or a whole number for a numeric one. The watermark itself never moves back. The read step says so, for example *incremental, modified after 2026-10-01T09:00:00+00:00 (the watermark 2026-10-01T11:00:00+00:00 less the lookback 2 hours)*. A row older than the lookback is still missed; a full rebuild (Backfill) picks it up. Changing `lookback` isn't a logic change.
- **`deletes`** compares keys with the source on every incremental run: it reads every key the source has (only the key columns, renamed, cast and cleaned as the table holds them) and finds the live keys in the table the source no longer has. This reads the source's whole key columns, which costs more than the watermark read, so it's opt-in.
  - `soft` keeps the row and marks it: the table gets an `is_deleted` (bool) and a `deleted_at` (datetime, UTC) column. Rows the source has are `is_deleted = false`; a deleted key's row is set `is_deleted = true` with `deleted_at` the run's start time in UTC, and keeps that time on later runs. A key that comes back at the source is live again. Turning `soft` on for a table that already exists rewrites it once to add the two columns (every row live). Don't list the two columns in the contract; a table that reads this one can list `is_deleted` to take it.
  - `hard` removes the row, in the same Delta MERGE commit as the new rows.
  - The run's **deletes** step says what it compared, for example *compared 999 source keys with 1000 live keys in the table: 1 key gone from the source to mark deleted*, and the write step adds *marked 1 row deleted* or *removed 1 row deleted at the source*. If the source has no rows at all, the run stops instead of marking or removing every row.
  - **Safety stops.** Before anything is written, the run stops with nothing marked or removed when:
    - more live keys would go than `max_deletes` allows. The default is `"20%"` of the table's live keys. Set `max_deletes = "50%"` or a whole number of keys under `[load]` when bigger deletes are real. A source that was cut short (a partial export, a truncated table) looks like a mass delete, and this is what catches it. On a small table a share stops on any delete: 20% of 4 live keys is 0 keys. Give a count instead, such as `max_deletes = 5`.
    - a source key can't be read as the key's type, or a clean rule blanks it. For example, the source's `id` turns into text holding `"three"`. Read leniently, that key would be empty and look gone. A `null_if` rule on a key column does this whenever the source has one of its values, so with deletes on, the run stops until that row is fixed at the source or the rule comes off.
    - the source has no live rows at all. In a table written with `deletes = "soft"`, a row marked `is_deleted = true` counts as gone.
  - Deletes are found on incremental runs. A first load, a full rebuild and a quarantine replay don't look for them; the next incremental run does.
  - An incremental table that reads a `soft` table doesn't get its deletes through the watermark: a marked row keeps its watermark value, so it isn't read again. Set `deletes` on the reading table too. Its comparison treats the keys marked `is_deleted = true` as gone. That applies to a source with both `is_deleted` (true or false) and `deleted_at`, the two columns `soft` writes. Another source's own `is_deleted` column is read as an ordinary column.

A run in Fabric uses the same engine, so it behaves the same. The settings are checked when you save: `lookback` and `deletes` need an incremental load of a Delta source with keys, `deletes` can't be used with a custom step or with keys this entity makes, a lookback is a duration or a whole number, and `max_deletes` is a share like `"20%"` or a whole number.

Full loads and rebuilds replace the whole table. A source too big to read at once can be read **in batches** (Preview): set `batch_by` under `[load]` to a column the source has, usually a date such as `order_date`. A full load, an incremental entity's first load, or a full rebuild then reads the source one batch of that column's values at a time, sorted, with as many rows in a batch as fit in free memory with the copies the later steps make. Each batch goes through every step (contract, keys, cleaning, derived columns, hooks, lookups, checks) on its own, and the batches are streamed into the table, which is replaced in **one Delta commit** at the end: readers see the old table until the whole load has worked, and a load that stops (a check, an error, a closed app) leaves the table as it was. When a check or an error stops a load on this PC, the data files the stream had already written are removed, and a **clean up** step says how many. That happens only if the run still holds the table and no other writer committed to it meanwhile; otherwise they, like the files of a closed app, are left for `VACUUM`, which removes files no version of the table uses. In OneLake nothing is removed and the clean up step says the files are left for `VACUUM`: a Fabric job may be writing the same table, and its files that aren't committed yet can't be told apart from this load's. The run shows a **plan batches** step, for example *8 batches by order_date, the largest 1,900,000 rows*, and one step per step name with the batches added up.

- Batching reads the source column once to count its values, then each batch's rows. When everything fits in one batch, the load is one ordinary read. Measured on a 15 GB PC with a 60 million row table (16 columns, about 13 GB in memory read at once, too big for one read): 6 batches, 52 seconds, peaking at 7.2 GB. The script is `examples/benchmark/batched_loads.py`.
- A batch never splits one value. When a single value holds more rows than fit, the memory guard stops the run and names that value: batch by a column with more values.
- `unique` checks also compare each batch with the values earlier batches wrote. A value repeated across batches fails the check in the later batch; in one big read every copy fails. With `on_fail = "stop"` both stop.
- Hooks see one batch at a time. A hook that needs the whole table (a ranking, a total) gives different results in batches: leave `batch_by` off for that entity.
- Only Delta sources (a table, a link or another entity) are batched; files are read as before. Dimensions (`scd1`, `scd2`) are merged by key and don't use it. Changing `batch_by` isn't a logic change.
- The table isn't partitioned by the column: batching is how the run reads, not how the table is stored. Parquet files a stopped load had already written stay in the table's folder: they aren't part of any table version, so nothing reads them, but they take space, and the app's Vacuum doesn't remove them yet.


**Before it reads a Delta source** (a table, a link or another entity), a run estimates the read from the table's Delta log alone: the files left after the watermark, their rows, their size on disk, and the memory the columns it reads take (8 bytes a number or timestamp, 4 a date, about 32 a text value). The **read** step shows it, for example *about 2,000 rows in 1 of 20 files (0.4 MB on disk, 0.1 MB in memory)*. If three times that estimate (the read plus the copies the later steps make; a 5-million-row first load peaked at 2.5 times) is more than the machine's free memory, the run stops before reading anything and says what to do instead: load it incrementally, read fewer columns, or run the environment in Fabric. To try anyway, set `memory_guard = false` under `[load]`; that isn't a logic change, so it doesn't ask whether to rebuild. Free memory is read on Windows and Linux; elsewhere there's no guard. File sources (`files`, `landing`) aren't estimated yet.

Measured on one Linux machine (4 cores, 16 GB), local Delta tables, one incremental run with 1,000 changed and 1,000 new rows, using [examples/benchmark/large_tables.py](https://github.com/KratosDataGod/KratosForge/blob/main/examples/benchmark/large_tables.py):

| Table | Changed rows | Write | Seconds | Peak memory | Files rewritten | Written |
| --- | --- | --- | --: | --: | --: | --: |
| 5 million rows, 20 files | in the latest file | MERGE (0.20) | 0.9 | 0.6 GB | 1 of 20 | 1.9 MB |
| | | rewrite (before 0.20) | 8.7 | 2.7 GB | 20 of 20 | 31 MB |
| 5 million rows, 20 files | spread over every file | MERGE (0.20) | 2.3 | 1.2 GB | 20 of 20 | 31 MB |
| | | rewrite (before 0.20) | 7.5 | 2.6 GB | 20 of 20 | 31 MB |
| 20 million rows, 40 files | in the latest file | MERGE (0.20) | 1.4 | 0.8 GB | 1 of 40 | 3.3 MB |
| | | rewrite (before 0.20) | 28.5 | 9.3 GB | 40 of 40 | 124 MB |
| Type 2 dimension, 5 million keys in 10 files, full snapshot source | in the latest file | MERGE (0.20) | 6.5 | 2.1 GB | 1 of 10 | 5.5 MB |
| | | rewrite (before 0.20) | 24.1 | 3.7 GB | 10 of 10 | 52 MB |
| Type 2 dimension, same | spread over every file | MERGE (0.20) | 7.2 | 2.2 GB | 10 of 10 | 52 MB |
| | | rewrite (before 0.20) | 23.9 | 3.8 GB | 10 of 10 | 52 MB |

Both ways end with the same rows. When the changed rows are spread over every file, a MERGE has to rewrite every file too, and the gain is mostly memory. Most of a dimension's remaining time and memory is reading its 5-million-row source snapshot, which both ways do. These are local Delta tables; OneLake adds network time to both.

### Schema changes (Preview)

This is how runs treat a table's schema, not a screen of its own, so **Settings > Preview features** has nothing to hide for it. A run changes a table's schema in one of three ways, and records every change.

- **A new column goes in place.** A column the contract adds, or one the drift policy keeps, joins the table in the same MERGE or append as the rows: no Parquet file is rewritten, and older rows read it as empty.
- **A wider type rewrites the table.** Widening a column in the contract (`int32` to `int64`, `float32` to `float64`, a decimal with more digits) reads the whole table and replaces it, and the write step says so. Delta has a type widening feature that would avoid the rewrite, but the Delta writer KratosForge uses can't write it yet, and it raises the table's Delta protocol beyond what Fabric's SQL analytics endpoint and Direct Lake are known to read, so it stays off.
- **A narrower type, or a type of another kind, is refused.** As Delta refuses it without `overwriteSchema`, a run that would change `int64` to `int32`, or a number to text, stops before it writes anything, and the table stays as it was. To make the change anyway, set `overwrite_schema = true` under `[contract]` for that run: the table is rewritten with the new types, and every report and table that reads the column sees them. Remove the setting afterwards so the next change is checked again.

Each change goes in `meta.schema_history` with the run, the **Delta version** that made it and the entity's **schema version** (1 for the table's first load, one more for each run that changes the schema). Its `change` is `created`, `added`, `widened`, `narrowed`, `retyped` or `dropped`, and `breaking` is true for the last three, the same rule promote's [breaking-change check](data-mesh.md#data-products) uses for a data product's ports. The **Schema drift** card lists the latest changes.

### Loads from files read only new files

An incremental or dimension load from files (`files` or `landing`) remembers each file it loaded: its path, size and modified time, in `LH_Meta`'s `meta.loaded_files`. Each run then reads only files that are new or have changed since, instead of every file in the folder. The **read** step says so, for example *1 new file of 30*.

- When every file is loaded already, the run succeeds without writing anything: *no new files*.
- A full backfill reads every file again and records them all.
- Full loads always read every file.
- To read every file on each run anyway, set `new_files_only = false` under `[source]`. This isn't a logic change, so it doesn't ask whether to rebuild.

The list is written after the table. A run that fails before its write reads the same files next time. The table's Delta commit also lists the files the run read, so if the table is written but the list can't be saved, the run says so and the next run still skips those files (and saves them to the list), with or without keys. This looks back over the table's last 20 commits, and only at runs that read 5,000 files or fewer.

**When a table's logic changes**, an incremental run stops and asks you to choose:

- **Apply to new rows only:** rows already loaded keep the old logic. `_run_id` shows which rows used which version.
- **Rebuild it and everything downstream** (recommended): reprocesses the full source, then every table that reads it, in order.
- **Migrate to Type 2** (when a dimension changes from Type 1 to Type 2): keeps every surrogate key and starts each row's history at 1900-01-01.

The table shows **Needs a decision** on Home and in Runs until you choose.

## 6. Quality: checks, quarantine and the four gates

Checks run before every write. Each has an `on_fail`:

| `on_fail` | What happens to failing rows |
| --- | --- |
| `stop` (default) | Nothing is written. The run fails with the check's name and how many rows failed. |
| `quarantine` | Failing rows go to `quarantine.<schema>_<table>` in the same lakehouse, with the check that held each one. The rest are written. |
| `warn` | Everything is written; the failures are recorded. |

**The four gates:**

1. **Save:** every entity is checked against its layer's standards. Columns must exist upstream, so renames and deletes show what breaks. Custom code needs an owner and a test.
2. **Write:** checks run before every commit. Only the framework writes a managed table; notebooks get an error and can use `scratch_` tables.
3. **Promote:** Dev to Test and Test to Prod need a clean save gate, passing tests, and a green last run of every table's current logic.
4. **After:** `meta.run_log`, `meta.dq_results`, `meta.watermarks`, `meta.schema_history` and `meta.deployments` in `LH_Meta`, plus `_run_id` and `_loaded_at` on every row and the run id and metadata version on every Delta commit.

The table commit comes first and everything in step 4 follows it, together with the quarantined rows. Only the table commit is ever retried, so a failure after it can't put the rows in twice: the run reports the rows as written and says what it couldn't record. Each commit also carries its watermark, so if saving `meta.watermarks` failed, the next run goes by the commit and doesn't load the same rows again. A run whose table write fails quarantines nothing.

**Exceptions.** When a rule genuinely doesn't fit, add an approved exception to `exceptions.toml` with the entity, the rule, a reason, an approver and an expiry date (or `permanent`). An expired exception fails the save gate again. **Data mesh > Quality > Exceptions** shows each one with the table's owner, the reason, the approver and the end date. One that ends within 14 days raises a notice on the bell and a banner on Data mesh, so it can be renewed or the rule met before runs and promote are blocked.

```toml
[[exception]]
entity = "gold.fact_sales"
rule = "custom_code_test"
reason = "Signed off by finance; test due next sprint"
approver = "Chris"
expires = "2026-11-01"
```

### Tools on the entity page

| Button | What it does |
| --- | --- |
| **Profile** | Reads what the entity reads, every row, on your PC, before cleaning: nulls, distinct values, ranges, lengths, most common values and what looks wrong. It suggests `[[clean]]` rules (trim, turn `N/A` into nulls, fold `Shipped`/`SHIPPED`, parse dates stored as text) and `[[check]]` rules (not null, unique, accepted values, ranges; warn by default, stop for the table's own key). Tick cleaning rules to see rows before and after; **Add to entity** appends the ticked blocks through the save gate. Problems a rule already handles are marked as handled and not suggested again. **Profile its table instead** profiles what was written. |
| **Compare** | Puts the table in two places side by side: Dev against Test or Prod, or now against an earlier Delta version. Row counts, keys only on one side, columns that changed value for the same key, and how each number column's total moved. Surrogate keys, run stamps and Type 2 dates are left out, since each environment issues its own. A table with more than 5,000,000 rows on either side isn't loaded into memory: KratosForge streams both sides and compares row counts, totals and a checksum of every row instead, and the result says **Compared: counts, totals and a checksum** rather than **Compared: every row by key**. A checksum that differs says some rows differ, not which keys. |
| **History** (Preview) | Every Delta version: when, what happened, which run. **Restore** brings back an earlier version (as a new version, so it can be undone too), moves an incremental table's watermark back, and offers to run the tables that read it. |
| **Schema drift** card | Reads the source now and lists new, gone and retyped columns. Add a new column to the contract, mark a gone column as renamed (`[source] rename`), take a new type, or set this table's drift policy. **Save to config** writes it through the save gate. |
| **Freshness** card | See [Freshness promises](#freshness-promises). |
| **Quarantine** card | Shows held-back rows and the check that held each. Fix values in place, or fix the check and save, then **Replay selected** (Preview): they go through the contract, derived columns and checks again, and are written as a `replay` run without moving the watermark. Rows that still fail stay in quarantine with the new reason. **Discard selected** removes rows that should never load. After a replay the app offers to rebuild downstream tables. |
| **Cell 1: surrogate keys** card | The Spark SQL MERGE that issues this table's keys, for running in Fabric. **Create template notebook** makes a notebook that starts with it. Once the environment's keys come from the platform (SQL_Platform, or SQL_Keys when Dev, Test and Prod share one keys database), there's no MERGE: the card says where keys come from, and runs from Data mesh take them from there. |

### Freshness promises

Freshness says when a table should have had a successful run, in the environments you pick (Test and Prod when left out):

```toml
[freshness]
within_hours = 26     # a successful run in the last 26 hours
by = "07:00"          # and one by 7:00 each day, this PC's clock
envs = ["prod"]
```

Late tables show on Home and in the bell. To get them in Teams, **Settings > Notifications > Teams alerts** (or **Set up Teams alerts** on the card) takes the address of a Teams Workflows webhook ("Post to a channel when a webhook request is received"). The app checks at startup and every 10 minutes while it's open. Late and failed tables become [incidents](user-guide.md#incidents-preview) (Preview), shared with everyone who can read the environment, and each new incident is posted once, and again when it recovers. An environment that a running [agent](agent.md#incidents-preview) already checks is left to the agent, so Teams hears about a problem once. With preview features hidden, each late or failed table is posted once, as before.

### Reliability objectives (Preview)

An objective says how often a table's runs must succeed:

```toml
[objectives]
criticality = "high"   # critical, high, standard or low
success_rate = 95      # percent of the last `runs` runs; from criticality when left out
runs = 20              # how many recent runs count (at least 5)
envs = ["prod"]        # Test and Prod when left out
```

Criticality sets the rate when `success_rate` is left out: critical 99 %, high 95 %, standard 90 %, low none. Skipped runs and runs still going don't count, and fewer than five finished runs say nothing. A table under its objective opens an "Objective missed" incident wherever incidents are evaluated (the [agent](agent.md#incidents-preview), for now). A completion time is the freshness promise's `by`.

### Tests (Preview)

**Data mesh > Quality > Tests** runs the `test_*.py` files in the project's `tests` folder. A test is a function named `test_something` that uses `assert`, as in pytest; it can take `tmp_path` for a scratch folder. Each run imports `transforms/` and `tests/` fresh, so it tests what you just saved. Promote runs them first and won't promote while one fails.

### Table health (Preview)

**Data mesh > Quality > Table health** reads every managed table's Delta log in the picked environment: files, size, average file size, V-Order, versions since the last VACUUM and the last OPTIMIZE, with advice.

- In **Test and Prod**, **Optimize** runs Fabric's table maintenance job on the workspace's capacity: OPTIMIZE with V-Order (what Direct Lake and the SQL endpoint read fastest), then VACUUM keeping the layer's history days. Tables written from a PC have no V-Order until it runs, so by default every table a Test or Prod run writes is optimized right after the run. Untick **Optimize every table a run writes** to stop that.
- In **Dev on this PC**, **Compact** does the same with delta-rs, without V-Order.

## 7. Environments and promote

**Data mesh > Deploy > Environments and promote** has a card for Dev, Test and Prod.

Promote to a Fabric workspace is in Preview: it is tested against local stores and has not yet been run on a tenant.

| On the card | What it does |
| --- | --- |
| **Assign workspace** / **Change** | Picks the environment's workspace, or **Create a new workspace** on a capacity you choose. The layer lakehouses and `LH_Meta` live there. |
| **Tables in: Workspace / This PC only** | Whether the environment reads and writes the workspace's lakehouses or the project's `.local` folder. |
| **Runs on: This PC / Fabric** | Where its runs execute. See below. |
| **Schedules** (Preview) | Runs every entity on a schedule, on this PC or in Fabric. |

Below the cards are **Promote Dev to Test** and **Promote Test to Prod**. Each shows whether the promote gate passes and, if not, what to do first. It also lists the tables whose logic differs from what last ran in the target, each with **Compare**, so you see what a promote does to the numbers before you press it. Under each table it lists what changed in its settings since the version promoted there, by meaning rather than by line: *column channel added (string)*, *check returns_are_negative changed on_fail from quarantine to stop*. Comments, spacing and the order of settings don't count.

**Promote to Test** (or Prod):

1. Checks the gate: the project passes the save gate, the tests pass, and every table's last run in the source environment is green on this exact metadata version.
2. Creates any missing lakehouses (`LH_<Layer>` for each layer, and `LH_Meta`) in the target workspace. Key tables: without a platform, it creates `LH_Keys` in the Keys workspace if it's missing, and any missing key tables in it. With the target's keys in the platform (the [one keys database](platform.md#one-keys-database-for-dev-test-and-prod) `SQL_Keys`, or the environment's `SQL_Platform`), a platform admin's promote creates the missing key tables, and anyone else's lists them as missing, to request on **Data mesh > Requests**.
3. Writes the source systems to `LH_Keys` (`config.source_system`, `config.source_connection`) when the target's keys are there; with keys in the platform, no source system tables are written. For a target in a Fabric workspace, it also publishes the `VL_Sources` variable library.
4. Pins the target to this metadata version, recorded in `meta.deployments` with a [release record](#release-records).
5. If the target runs in Fabric, uploads the new version for its runner.

Promote copies **configuration**, never data and never keys. With one keys database, Dev, Test and Prod issue from the same key tables, so a business key has the same surrogate key everywhere; otherwise each environment issues its own: in its `SQL_Platform`, in `LH_Keys` in its Keys workspace, or, for an environment that runs on this PC, in key tables on this PC. Test and Prod load from their own sources. After a promote, run the target to load it.

### Release records

*Preview.* Every promote records which Git commit it came from, so you can tell later exactly which files are running in Test or Prod. KratosForge reads Git with the `git` installed on your PC: it never signs in, never fetches and never stores a remote's address. "Not pushed" and "behind" are counted against your upstream branch as of your last fetch.

The record goes into the target's `meta.deployments` row:

| Column | Meaning |
| --- | --- |
| `commit`, `branch` | The commit and branch the project folder was on. Empty when the folder isn't in Git |
| `dirty` | True when files that make up the metadata version differed from that commit. Local data (`.local`), caches and `environments.toml` don't count |
| `pushed` | True when the commit was on the upstream branch |
| `engine_version`, `lock_hash` | The engine version, and a hash of the versions of the packages it computes with (Polars, delta-rs, DuckDB, PyArrow) |
| `by` | The PC login name that promoted. The receipt's `identity` also has `object_id`, the promoter's Microsoft Entra object id, when they were signed in |
| `receipt`, `receipt_id` | The whole record as a [run receipt](notebooks.md) of kind `release`, and its SHA-256 id. Anyone with the row can recompute the id to check the record wasn't changed |

Rows written before this existed have these columns empty.

### Released files and rollback

*Preview.* A promote also keeps every file of the metadata version (configs, transforms, tests: the files the version is a hash of, without `environments.toml` and `.local`) in the target's `meta.release_files`. Test and Prod then run those files, wherever the run happens: on this PC, on an agent, or in Fabric. Editing your project folder changes what Dev runs, never what Test or Prod run, until you promote again. The first run after a release writes its files once to `.local/releases/<env>/<version>/` in the project folder and reuses them. An environment last promoted before releases kept their files runs your folder as before, and only while the folder is on that release.

**Roll back...** on a Test or Prod **Last release** card puts that environment back on an earlier release it kept. It lists every release to the environment, shows what goes back entity by entity, and then:

- puts the environment on the earlier release's files, exactly as that environment kept them. Nothing is read from Git, Dev or your folder, and files that don't match their recorded hashes are refused
- records the rollback as a new release in `meta.deployments`, with a release record of its own whose `rollback` section names the release it replaced and the one it went back to
- uploads the earlier files for Fabric runs when the environment runs in Fabric

Tables keep their rows. A table whose logic changes asks whether to rebuild on its next run; to put its rows back now, restore it from **History**. Rolling back checks your workspace roles there, as a promote does. It doesn't apply `separate_promoter`, because it only goes back to a release that was already promoted. Releases promoted before this version can't be rolled back to: promote them again instead.

Each promote card shows the **Last release** to its target: the metadata version, the commit and whether it was pushed, the engine, who promoted it and when, and whether the stored record still matches its id (**checks out**). **Copy record** copies the whole record.

Test and Prod in OneLake only take released files. If the files can't be named by a pushed commit (uncommitted changes, unpushed commits, no upstream branch, or no Git), a promote to an environment whose tables are in OneLake is blocked until they are committed and pushed. An environment kept on this PC goes ahead, and the promote card says so. `require_commit` in `environments.toml` changes this for one environment:

```toml
[test]
require_commit = false   # go ahead with a note instead

[prod]
require_commit = true    # block even when this environment is on this PC
```

### Who can promote

*Preview*, part of **Promote** and tagged with it. Before a promote to Test or Prod writes anything, the promote card checks your workspace role in each workspace the target uses. If Fabric lists you as a **Viewer** there, it says so and names the workspace's Admins to ask, and the promote is blocked. Fabric would refuse those writes anyway, partway through. If your role may come through a group, or Fabric won't show the workspace's access list, the card shows a note instead and Fabric decides when the promote writes.

To have a second person release to Prod, set `separate_promoter = true` for Prod in `environments.toml`:

```toml
[prod]
separate_promoter = true
```

Whoever promoted a metadata version to Test then can't also promote it to Prod. It counts you as the same person when your Microsoft Entra object id matches the one in the [release record](#release-records)'s receipt, which catches one person on two PC logins, or when your PC login name matches the `by` column, so signing in with a second account on the same login doesn't get around it. A login several people share counts as one person. When either id is missing (you aren't signed in, or the version was promoted to Test before releases recorded the id) only the login names are compared, and the promote card says so. Either way it's a team process check, not a security boundary, and `environments.toml` isn't part of the metadata version, so whoever edits it can turn the check off: to make sure only release managers can change Prod, give only them Contributor or higher on the Prod workspaces.

### This PC or Fabric runs each environment

Each environment card has **Runs on: This PC | Fabric**, saved as `compute` in `environments.toml`. Fabric needs the tables in OneLake, so an environment with **This PC only** tables runs on the PC until you point it at a workspace.

With **Fabric** (Preview), **Run all** (and running one entity, backfills and rebuilds) uploads the metadata project and the app's engine to `LH_Meta/Files/_fabric_desktop/current`, and starts one notebook in the workspace, `nb_fabric_desktop_runner`. It's a Fabric Python notebook that runs the same engine against the same lakehouses. The upload is skipped when nothing changed. The app follows the job and shows the results when it finishes; the run log, checks and quarantine land in the same `LH_Meta` tables, so it doesn't matter where a run happened. The runner fails its Fabric job when a table fails, so Fabric's Monitoring hub shows it too. A Fabric schedule runs every entity even when your PC is off, from the project as it was last uploaded.

## 8. Surrogate keys

Surrogate keys are issued in one place. The recommended setup is [one keys database for Dev, Test and Prod](platform.md#one-keys-database-for-dev-test-and-prod) (Preview), in one Keys workspace, so a business key has the same surrogate key in every environment. Without the [platform](platform.md), each environment issues its own keys in `LH_Keys`, in a dedicated Keys workspace (Dev keeps it on the PC); the rest of this section describes that setup. Each business key has its own key table, `sk.<name>`, with three columns: `<name>_sk` (INT), `<name>_bk` (the business key, unique) and `create_date`. Customer ABC gets one key, say 123, and every source that has Customer ABC maps to 123.

**Sync to LH_Keys** is in Preview for Fabric environments: it has not yet been run on a tenant.

**Data mesh > Design > Surrogate keys:**

- **New key table** adds one, for example `customer`.
- **Pick Keys workspace** (or **Change**) sets where `LH_Keys` lives for the picked environment.
- The list shows each key table's key count, next key, and which tables use it. Select one to **look up a business key**, see its attributes, and copy the block that makes an entity use it.
- **Sync to LH_Keys** creates any missing key tables and writes the source system configuration.

An entity takes keys with a `[[keys]]` block. The first step of every run MERGEs business keys it hasn't seen into the key table (continuing from the highest key, never reusing one), then adds `<name>_sk` to every row. Rows with no business key get -1 (the Unknown member). A Type 1 dimension whose rows already carry the key uses it instead of issuing its own. A composite key is a list, stored as `"a||b"`. Entities that share a key table can run at the same time, on one PC or several: each MERGE is committed against the version of the key table its new keys were worked out from, and is redone if another run committed first, so no key goes to two business keys. A run checks the key table for a key or business key that appears twice and stops, writing nothing, if it finds one.

```toml
[[keys]]
table = "customer"
business_key = "customer_id"     # or ["source_code", "customer_id"]
```

### Keys in the platform

With a [platform](platform.md) in Fabric, an environment's keys come from the platform instead, one key system for every team: from `SQL_Keys` once they've moved into the [one keys database](platform.md#one-keys-database-for-dev-test-and-prod), shared by Dev, Test and Prod, and from that environment's `SQL_Platform` until then. A project that already issued keys in `LH_Keys` moves them with **Move keys** on this tab: every key keeps its number, each row is checked, and only then does the environment switch (`keys = "platform"` in `environments.toml`).

In an environment whose keys are in the platform, the project doesn't make its own key tables. **Use a key table** picks one of the platform's (it still writes `metadata/keys/<name>.toml`, so the project says which keys it uses). Only platform admins add key tables: **Request a new one** sends a request to **Data mesh > Requests**, and the key table shows in the list once it's approved. A run that needs a key table the platform doesn't have stops and says to request it; **Sync** lists any that are missing.

## 9. Source systems and the variable library

**Data mesh > Design > Source systems** keeps every source's fixed id, code (like `CRM`), name, type, owner and active flag, and its connection in Dev, Test and Prod: server, port, database, schema, base URL, file path, authentication, tenant, username, Key Vault URL and secret name, Fabric connection id, gateway and extra options. Switch environment with the Dev / Test / Prod buttons above the connection fields.

With Preview features on, the platform's list is the one list of source systems: **Add a source this project reads** picks a source from it with its id, code, name, owner and type, and the project keeps only its connection. Sources made before the platform's list existed show under "Only in this project" until **Move sources to the platform** copies them there with the same ids. See [One list of source systems](platform.md#one-list-of-source-systems-preview).

**Passwords and keys never go here.** Give the Key Vault URL and the secret's name; the notebook reads the secret when it runs.

**Sync to LH_Keys** writes them to `config.source_system` and `config.source_connection`, one row per source per environment, so notebooks and pipelines can look up connection values by code.

**Variable library VL_Sources** (Preview, on the same tab; not yet run on a tenant) is one Fabric variable library per environment workspace, with that environment's values active. **Publish to Dev / Test / Prod** creates or updates it; promote updates it in the target. Notebooks read the same names in Fabric and on your PC:

```python
lib = notebookutils.variableLibrary.getLibrary("VL_Sources")
lib.CRM_server_host
```

## 10. Semantic model (Preview)

**Data mesh > Deploy > Semantic model** builds a Direct Lake semantic model over a layer's tables (the last layer, usually Gold) in Test or Prod. It reads the layer's lakehouse through its SQL analytics endpoint, with no import and no refresh schedule.

- Each entity is a table; dimensions and facts are marked. Facts get a row count measure.
- No column sums on its own (`summarizeBy: none`, as Best Practice Analyzer's rules ask). Instead each fact gets a total measure for every visible numeric column that isn't a key, named like its row count measure: `fact_sales total net_amount`. Names don't change as the model grows, so reports keep their measures. Visuals that already sum a column keep their aggregation.
- Relationships come from the metadata: a fact's `[[lookup]]` names the dimension and key, and any other column named like a dimension's surrogate key joins to it.
- Surrogate keys, `_run_id` and `_loaded_at` are hidden.
- **Date table.** A date dimension becomes the model's date table (*Mark as date table* in Power BI, so time intelligence like `TOTALYTD` works) when its entity names the date column:

  ```toml
  [model]
  date_column = "day"
  ```

  The entity must be a dimension (`scd1` or `scd2` with a surrogate key) and the column a contract column of type `date` or `datetime`. Power BI refuses a date table with a blank date, so this dimension's Unknown member gets the date 1899-12-30 (day 0 of Power BI's date serials) instead of a blank one. A dimension that already exists is rewritten once on its next run to give its Unknown member that date (the write step says so); later runs merge as usual. Facts still point unmatched rows at -1, and visuals show them under 1899-12-30.
- Two entities whose Delta tables share a name are qualified by schema (`sales_orders`). **Update model** first reads the model's definition and keeps the names its tables already have, so when a second `orders` table arrives only the newcomer is qualified, and reports keep their tables and measures. If the definition can't be read, nothing is written.
- **Create model** makes `SM_<Layer>` in the environment's workspace (rename it if you like). **Update model** updates it in place and keeps every lineage tag, so reports built on it keep working. The app asks the SQL endpoint to pick up new tables first.
- If a model with that name already exists (for example after a rename), KratosForge asks first: the confirmation names the model and says its definition will be replaced, including measures, tables and relationships added to it in Fabric. Nothing is written until you confirm.

**When Fabric can't be read.** KratosForge doesn't guess whether a model exists or what a table holds:

- If the workspace's semantic models can't be listed (no access, throttling, a sign-in that failed), the app shows the reason and the tab offers neither **Create model** nor **Update model**. Try again once Fabric answers.
- A table that hasn't been written in the environment yet is left out, and the result says so (**Not written yet, so left out**).
- A column whose type Direct Lake can't read (arrays, maps, structs) is left out of its table, and the result names each one with its type. A relationship that would use such a column is left out too, and named, rather than pointing at a column the model doesn't have.
- A table that exists but can't be read stops the build with the table's name and the reason, and nothing is written, so the model never silently loses a table.

Dev on the PC has no Direct Lake model: switch the environment to Test or Prod.

## 11. Bring existing tables under management

To manage tables that already exist in a lakehouse, use **Adopt** on the lakehouse in the Estate X-ray on Home (Preview). It writes an entity for each table with `[source] table` pointing at the existing table and a contract from its columns. Review the files, then run them.

## 12. Run on a schedule or a server

- **On this PC or in Fabric:** **Schedule** at the top of Metadata, or **Schedules** on an environment card (Preview). Listed in **Monitor > KratosForge > Schedules and Monitor > Fabric > Schedules**.
- **On a server, unattended:** the [headless agent](agent.md) runs the same engine on Windows or Linux as a service principal or managed identity, with a durable queue and retries. It hasn't yet run against a live tenant.

## Reference

### Entity file

`metadata/entities/<layer>.<table>.toml`. An example with the common sections:

```toml
name = "silver.orders"
owner = "data-team@contoso.com"
description = "Orders, cleaned"

[source]
entity = "bronze.orders"          # or files = "landing/orders/*.csv", landing = "erp/orders/*.csv" (the
                                  # platform's landing zone), table = "<abfss uri>", or link = "<link name>"
rename = { "cust_id" = "customer_id" }

[load]
mode = "incremental"              # full | incremental | scd1 | scd2
watermark = "modified_at"

[contract]
keys = ["order_id"]
drift = "ignore_new"              # evolve | ignore_new | strict; overrides the layer

[contract.columns]
order_id = "int64"
customer_id = "int64"
order_date = "date"
amount = "float64"
status = "string"
modified_at = "date"
net_amount = "float64"

[[clean]]
column = "status"
trim = true
case = "lower"

[[clean]]
dedupe_keep = "newest:modified_at"

[[derive]]
column = "net_amount"
expr = "amount - coalesce(discount, 0)"

[[check]]
column = "order_id"
not_null = true
unique = true
on_fail = "stop"

[[keys]]
table = "order"
business_key = "order_id"
```

| Section | Key | Meaning |
| --- | --- | --- |
| top level | `name`, `owner`, `description` | The entity's name (`layer.table`), who owns it, and what it is |
| `[source]` | `files` | A file pattern under the layer's lakehouse `Files` |
| | `entity` | Another entity it reads |
| | `table` | An existing Delta table, by OneLake URI (adopted tables) |
| | `rename` | Source column names mapped to the contract's names |
| | `new_files_only` | `false` makes an incremental or dimension load read every file each run ([Loads from files](#loads-from-files-read-only-new-files)) |
| `[load]` | `mode` | `full`, `incremental`, `scd1` or `scd2` |
| | `watermark` | The column incremental loads move forward on |
| | `memory_guard` | `false` lets a run start a read that's estimated not to fit in free memory ([Large tables](#large-tables)) |
| | `batch_by` | A source column: full loads and rebuilds read the source in batches of its values and replace the table in one commit ([Large tables](#large-tables)) |
| | `lookback` | How far before the watermark an incremental run reads again, so late rows land: `"2 hours"`, `"30 minutes"`, `"3 days"`, or a whole number for a numeric watermark ([Late rows and source deletes](#late-rows-and-source-deletes-preview)) |
| | `deletes` | `none` (the default), `soft` or `hard`: what an incremental run does with a key the source no longer has ([Late rows and source deletes](#late-rows-and-source-deletes-preview)) |
| | `max_deletes` | The most live keys one run may mark or remove before it stops, as a share (`"20%"`, the default) or a whole number of keys ([Late rows and source deletes](#late-rows-and-source-deletes-preview)) |
| `[contract]` | `keys` | The business key columns |
| | `drift` | This table's schema drift policy |
| | `overwrite_schema` | `true` lets a run narrow a column's type or change it to another kind, rewriting the table (Preview; [Schema changes](#schema-changes-preview)) |
| `[contract.columns]` | `<column> = "<type>"` | `string`, `int64`, `float64`, `bool`, `date`, `datetime`, `decimal(p,s)` |
| `[[clean]]` | `column` plus `trim`, `case` (`lower`, `upper`, `title`), `replace` (`{ "old" = "new" }`), `null_if` (a list), `parse_date` (formats like `"dd/MM/yyyy"`), `fill_null`, `round` | Cleaning rules, applied in order |
| | `dedupe_keep` | `"newest:<column>"` or `"oldest:<column>"`: keep one row per key |
| `[[derive]]` | `column`, `expr` | A new column from a SQL expression |
| `[[check]]` | `column` with `not_null`, `unique`, `accepted_values` (a list), `range` (`{ min = 0, max = 100 }`) or `exists_in` (`"layer.table.column"`) | Column checks |
| | `name`, `expr` | A row rule as a SQL expression |
| | `python`, `owner`, `test` | A custom check |
| | `on_fail` | `stop`, `quarantine` or `warn` |
| `[[keys]]` | `table`, `business_key` | Surrogate keys from the environment's key tables (`SQL_Keys`, `SQL_Platform` or `LH_Keys`) |
| `[[lookup]]` | `dim`, `on`, `as`, `at` | A dimension's surrogate key as of the fact's date |
| `[scd]` | `surrogate_key`, `tracked`, `type1` | Dimension settings |
| `[model]` | `date_column` | Makes the dimension the semantic model's date table (see [section 10](#10-semantic-model-preview)) |
| `[hooks]` | `after_clean`, `before_write`, `reads`, `adds`, `owner`, `test` | Your Python at a fixed point |
| `[step]` | `custom`, `reads`, `owner`, `test` | Your Python replaces the transform |
| `[params]` | any | Values your code reads from `ctx.params` |
| `[freshness]` | `within_hours`, `by`, `envs` | When a successful run is due |
| `[objectives]` | `criticality`, `success_rate`, `runs`, `envs` | How often runs must succeed (Preview) |

Every written row also gets `_run_id` and `_loaded_at`.

### layers.toml

An optional `[rules]` section (`same_layer_reads = "dag"` lets a table read its own layer when that makes no loop; `"never"` forbids it), then one `[[layer]]` per layer, in order, with `name`, `description`, `drift`, `required_checks` (`key_not_null`, `key_unique`), `reads_from`, `require_keys`, `retention_days` and `workspace` (a designation: `ingest`, `product`, `serve` or `sandbox`; see [Data mesh](data-mesh.md#workspaces-by-designation)). The Layers tab writes this file for you.

### environments.toml

One section per environment (`[dev]`, `[test]`, `[prod]`):

| Key | Meaning |
| --- | --- |
| `workspace_id`, `workspace_name` | The environment's workspace |
| `target` | `local` (the `.local` folder) or `onelake` (the workspace) |
| `compute` | `local` (this PC) or `fabric` |
| `keys_workspace_id`, `keys_workspace_name` | Where `LH_Keys` lives |
| `optimize_after_run` | Optimize every table a run writes (Test and Prod) |
| `keys` | `platform` (keys in the platform: `SQL_Keys` when the organization has one keys database, else `SQL_Platform`) or `lakehouse` (`LH_Keys`). Unset, keys come from the platform once the environment has one in Fabric, unless the project already has `LH_Keys` |
| `workspaces` | A workspace per designation, like `{ ingest = { id = "...", name = "Sales Ingest - Test" } }` |
| `require_commit` | Whether promote here takes only files a pushed Git commit holds. On by default for an environment in OneLake, off for one on this PC; `false` or `true` overrides it ([Release records](#release-records)) |
| `separate_promoter` | `true`: whoever promoted this version to the environment before can't promote it here ([Who can promote](#who-can-promote)) |

Changing this file doesn't change the metadata version, so it never forces a re-promote.

No two environments can use the same workspace, as their own `workspace_id` or as a designation's: each environment's lakehouses have the same names (`LH_Meta`, `LH_Bronze`, ...), so a run in one would write the other's tables. Saving such a change is refused, and if the file is edited by hand to share one, both environments refuse to read or write until it's fixed.

A [job file](notebooks.md) run with `fabric-desktop run job.toml` can't write a table the metadata project manages, or anything in a workspace that Test or Prod uses: those change only through a framework run and promote.

### Data mesh files

`project.toml` (the project's domain and team), `metadata/products/<name>.toml` (data products) and `metadata/links/<alias>.toml` (links to other teams' products), plus an optional `[catalog]` block in an entity file. All are described in [Data mesh](data-mesh.md).

### Column lineage (Preview)

KratosForge works out where every column comes from by reading the project's files, without running anything. Each column is one of: read from the source (by the same name, or through `rename`), a `[[derive]]` expression and the columns it uses, a `[[lookup]]` of a dimension's key, a `[[keys]]` surrogate key, or added by the framework. Each is labelled **declared** when a file states the link (a rename, an expression, a lookup, a key) or **inferred** when it is assumed from a matching name. Columns your Python adds (`[hooks] adds`, or every column of a `[step] custom`) are **opaque**: they're never guessed. Columns a hook runs after are flagged, because a hook can change any column. For one column, lineage follows the steps back to the landing files and finds every column built from it further on. On an entity's page, **Columns** in the Lineage card lists every column with how it's made and its label; pick one to see where it comes from and what reads it.

### Columns and tables the framework adds

| Where | What |
| --- | --- |
| Every row | `_run_id`, `_loaded_at` |
| Type 2 dimensions | `valid_from`, `valid_to`, `is_current` |
| Dimensions | The surrogate key column, and an Unknown member at -1 (dated 1899-12-30 in the model's date table) |
| `LH_Meta` | `meta.run_log`, `meta.dq_results`, `meta.watermarks`, `meta.schema_history`, `meta.deployments` |
| Each layer lakehouse | `quarantine.<schema>_<table>` for held-back rows |
| `LH_Keys` | `sk.<name>` per key table; `config.source_system`, `config.source_connection` |

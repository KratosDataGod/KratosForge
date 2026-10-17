# Metadata framework guide

The metadata framework builds and runs your medallion layers (Bronze, Silver, Gold, or any 2 to 10 layers you choose) from configuration instead of one-off notebooks. You describe each table once in a small file: where its data comes from, its columns, how to clean and check it, and how to load it. Fabric Desktop by KratosBI runs every table the same way, checks it, writes it to its layer's lakehouse, and logs what happened.

This guide goes from an empty folder to Prod. The [reference](#reference) at the end lists every setting.

> **Tested and not yet tested.** Everything that runs on your PC, including Dev on the PC, the four gates and every load mode, is covered by automated tests on every build. Features that talk to Fabric (promote to Test and Prod, runs in Fabric, Optimize, the semantic model, history and restore, the variable library) are built to Fabric's documented APIs but haven't yet been tried on a live tenant. Several are tagged **Preview** in the app.

## Ideas in one page

| Idea | What it means |
| --- | --- |
| **Project** | A folder you keep in Git. Every table is a file in it. |
| **Layer** | A stage of your data (Bronze, Silver, Gold). Each layer is its own schema-enabled lakehouse, `LH_<Layer>`, in every environment. Layers never mix. |
| **Entity** | One managed table, described by `metadata/entities/<layer>.<table>.toml`. |
| **Environment** | Dev, Test and Prod. Each has its own workspace (Dev can stay on your PC). |
| **Run** | Reading, checking and writing one or more entities in one environment. Every run goes through the same steps: read, contract, surrogate keys, clean, derive, hooks, lookups, checks, write, log. |
| **Gates** | Rules enforced at four points (save, write, promote, after) so the standards are followed without anyone policing them. |
| **Promote** | Moving a tested metadata version from Dev to Test, or Test to Prod. |
| **LH_Meta** | A lakehouse per environment that holds the run log, check results, watermarks, schema history and deployments. |
| **LH_Keys** | A lakehouse per environment, in a shared Keys workspace, that issues surrogate keys. |

## 1. Set up a project

1. Open **Data mesh** in the left rail. With no project yet, it shows **Set up a metadata project**.
2. **Folder:** where the project goes. The default is `Documents\FabricDesktop\metadata`. Choose a folder inside a Git repository if you have one.
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
| **Surrogate keys: Required** | Every table in the layer must take its keys from LH_Keys. |
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
| Type 1 dimension | `scd1` | Merges on the key, overwriting changes. Surrogate keys are issued once and never renumbered. An Unknown member is at -1. |
| Type 2 dimension | `scd2` | A change in a `tracked` column closes the current row and adds a new version with `valid_from`, `valid_to` and `is_current`. Columns in `type1` are overwritten on every version. |

Facts use `[[lookup]]` to take a dimension's surrogate key as it was on the fact's date. A fact row whose dimension member doesn't exist gets -1.

### Large tables

An incremental run is built to stay small when the table is big:

- **It reads only what it needs.** The watermark filter goes into the read itself, so files whose rows are all older are skipped without being opened. A table that reads another entity reads only the columns it uses (plus any new ones, so drift is still noticed). The run's **read** step says so, for example *incremental, modified after 2026-10-01; read 3 columns of 4*.
- **It rewrites only what changed.** New and changed rows go into the table with a Delta MERGE on the key: rows with a key already in the table are replaced, the rest are added, and Parquet files without any of those keys stay as they are. A table without keys gets a plain append. The **write** step says what happened, for example *merged 2000 rows on id: 1000 updated, 1000 new (Delta MERGE rewrote 1 file, added 1)*.
- **It falls back to a rewrite when a MERGE would change the result.** If the new rows repeat a key (there's no `unique` check), or a column's type changed, the run reads the whole table and replaces it, as every run did before 0.20, and the write step says why.

Replayed quarantine rows go in the same way.

**Dimensions** (`scd1`, `scd2`) read only the rows of the business keys that arrived, plus the Unknown member, merge them exactly as before (same keys, versions and Type 2 checks) and write them back with one MERGE on the surrogate key. A Type 2 dimension fed a full snapshot of its source first drops the rows that would change nothing (same tracked values, no new Type 1 value), so only changed and new keys are touched; when none changed, nothing is written at all. A Type 1 dimension takes every row that arrives, so a full snapshot rewrites it, and the write step says *most of it changed*. The write step says what it read, for example *1000 new, 1000 changed (new versions), 0 Type 1 updates, 4999000 rows unchanged; read 1001 rows of the table for 2000 business keys*.

Full loads and rebuilds replace the whole table. A source too big to read at once can be read **in batches** (Preview): set `batch_by` under `[load]` to a column the source has, usually a date such as `order_date`. A full load, an incremental entity's first load, or a full rebuild then reads the source one batch of that column's values at a time, sorted, with as many rows in a batch as fit in free memory with the copies the later steps make. Each batch goes through every step (contract, keys, cleaning, derived columns, hooks, lookups, checks) on its own, and the batches are streamed into the table, which is replaced in **one Delta commit** at the end: readers see the old table until the whole load has worked, and a load that stops (a check, an error, a closed app) leaves the table as it was. The run shows a **plan batches** step, for example *8 batches by order_date, the largest 1,900,000 rows*, and one step per step name with the batches added up.

- Batching reads the source column once to count its values, then each batch's rows. When everything fits in one batch, the load is one ordinary read. Measured on a 15 GB PC with a 60 million row table (16 columns, about 13 GB in memory read at once, too big for one read): 6 batches, 52 seconds, peaking at 7.2 GB. The script is `examples/benchmark/batched_loads.py`.
- A batch never splits one value. When a single value holds more rows than fit, the memory guard stops the run and names that value: batch by a column with more values.
- `unique` checks also compare each batch with the values earlier batches wrote. A value repeated across batches fails the check in the later batch; in one big read every copy fails. With `on_fail = "stop"` both stop.
- Hooks see one batch at a time. A hook that needs the whole table (a ranking, a total) gives different results in batches: leave `batch_by` off for that entity.
- Only Delta sources (a table, a link or another entity) are batched; files are read as before. Dimensions (`scd1`, `scd2`) are merged by key and don't use it. Changing `batch_by` isn't a logic change.
- The table isn't partitioned by the column: batching is how the run reads, not how the table is stored. Parquet files a stopped load had already written stay in the table's folder: they aren't part of any table version, so nothing reads them, but they take space, and the app's Vacuum doesn't remove them yet.


**Before it reads a Delta source** (a table, a link or another entity), a run estimates the read from the table's Delta log alone: the files left after the watermark, their rows, their size on disk, and the memory the columns it reads take (8 bytes a number or timestamp, 4 a date, about 32 a text value). The **read** step shows it, for example *about 2,000 rows in 1 of 20 files (0.4 MB on disk, 0.1 MB in memory)*. If three times that estimate (the read plus the copies the later steps make; a 5-million-row first load peaked at 2.5 times) is more than the machine's free memory, the run stops before reading anything and says what to do instead: load it incrementally, read fewer columns, or run the environment in Fabric. To try anyway, set `memory_guard = false` under `[load]`; that isn't a logic change, so it doesn't ask whether to rebuild. Free memory is read on Windows and Linux; elsewhere there's no guard. File sources (`files`, `landing`) aren't estimated yet.

Measured on one Linux machine (4 cores, 16 GB), local Delta tables, one incremental run with 1,000 changed and 1,000 new rows, using [examples/benchmark/large_tables.py](https://github.com/KratosDataGod/fabric-desktop/blob/main/examples/benchmark/large_tables.py):

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

### Loads from files read only new files

An incremental or dimension load from files (`files` or `landing`) remembers each file it loaded: its path, size and modified time, in `LH_Meta`'s `meta.loaded_files`. Each run then reads only files that are new or have changed since, instead of every file in the folder. The **read** step says so, for example *1 new file of 30*.

- When every file is loaded already, the run succeeds without writing anything: *no new files*.
- A full backfill reads every file again and records them all.
- Full loads always read every file.
- To read every file on each run anyway, set `new_files_only = false` under `[source]`. This isn't a logic change, so it doesn't ask whether to rebuild.

The list is written after the table. A run that fails before its write reads the same files next time. If the table is written but the list can't be, the run says so and the next run reads those files again, which a load with keys absorbs.

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
| **Profile** | Reads what the entity reads, every row, on your PC, before cleaning: nulls, distinct values, ranges, lengths, most common values and what looks wrong. It suggests `[[clean]]` rules (trim, turn `N/A` into nulls, fold `Shipped`/`SHIPPED`, parse dates stored as text) and `[[check]]` rules (not null, unique, accepted values, ranges). Tick cleaning rules to see rows before and after; **Add to entity** appends the ticked blocks through the save gate. **Profile its table instead** profiles what was written. |
| **Compare** | Puts the table in two places side by side: Dev against Test or Prod, or now against an earlier Delta version. Row counts, keys only on one side, columns that changed value for the same key, and how each number column's total moved. |
| **History** (Preview) | Every Delta version: when, what happened, which run. **Restore** brings back an earlier version (as a new version, so it can be undone too), moves an incremental table's watermark back, and offers to run the tables that read it. |
| **Schema drift** card | Reads the source now and lists new, gone and retyped columns. Add a new column to the contract, mark a gone column as renamed (`[source] rename`), take a new type, or set this table's drift policy. **Save to config** writes it through the save gate. |
| **Freshness** card | See [Freshness promises](#freshness-promises). |
| **Quarantine** card | Shows held-back rows and the check that held each. Fix values in place, or fix the check and save, then **Replay selected**: they go through the contract, derived columns and checks again, and are written as a `replay` run without moving the watermark. **Discard selected** removes rows that should never load. After a replay the app offers to rebuild downstream tables. |
| **Cell 1: surrogate keys** card | The Spark SQL MERGE that issues this table's keys, for running in Fabric. **Create template notebook** makes a notebook that starts with it. |

### Freshness promises

Freshness says when a table should have had a successful run, in the environments you pick (Test and Prod when left out):

```toml
[freshness]
within_hours = 26     # a successful run in the last 26 hours
by = "07:00"          # and one by 7:00 each day, this PC's clock
envs = ["prod"]
```

Late tables show on Home and in the bell. To get them in Teams, **Settings > Notifications > Teams alerts** (or **Set up Teams alerts** on the card) takes the address of a Teams Workflows webhook ("Post to a channel when a webhook request is received"). Each late or failed table is posted once. The app checks at startup and every 10 minutes while it's open.

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

| On the card | What it does |
| --- | --- |
| **Assign workspace** / **Change** | Picks the environment's workspace, or **Create a new workspace** on a capacity you choose. The layer lakehouses and `LH_Meta` live there. |
| **Tables in: Workspace / This PC only** | Whether the environment reads and writes the workspace's lakehouses or the project's `.local` folder. |
| **Runs on: This PC / Fabric** | Where its runs execute. See below. |
| **Schedules** (Preview) | Runs every entity on a schedule, on this PC or in Fabric. |

Below the cards are **Promote Dev to Test** and **Promote Test to Prod**. Each shows whether the promote gate passes and, if not, what to do first. It also lists the tables whose logic differs from what last ran in the target, each with **Compare**, so you see what a promote does to the numbers before you press it. Under each table it lists what changed in its settings since the version promoted there, by meaning rather than by line: *column channel added (string)*, *check returns_are_negative changed on_fail from quarantine to stop*. Comments, spacing and the order of settings don't count.

**Promote to Test** (or Prod):

1. Checks the gate: the project passes the save gate, the tests pass, and every table's last run in the source environment is green on this exact metadata version.
2. Creates any missing lakehouses (`LH_<Layer>` for each layer, and `LH_Meta`) in the target workspace, and `LH_Keys` with any missing key tables in the Keys workspace.
3. Syncs source systems to `LH_Keys` and publishes the `VL_Sources` variable library.
4. Pins the target to this metadata version, recorded in `meta.deployments` with a [release record](#release-records).
5. If the target runs in Fabric, uploads the new version for its runner.

Promote copies **configuration**, never data and never keys. Each environment issues its own surrogate keys from its own `LH_Keys`, and Test and Prod load from their own sources. After a promote, run the target to load it.

### Release records

*Preview.* Every promote records which Git commit it came from, so you can tell later exactly which files are running in Test or Prod. Fabric Desktop by KratosBI reads Git with the `git` installed on your PC: it never signs in, never fetches and never stores a remote's address. "Not pushed" and "behind" are counted against your upstream branch as of your last fetch.

The record goes into the target's `meta.deployments` row:

| Column | Meaning |
| --- | --- |
| `commit`, `branch` | The commit and branch the project folder was on. Empty when the folder isn't in Git |
| `dirty` | True when files that make up the metadata version differed from that commit. Local data (`.local`), caches and `environments.toml` don't count |
| `pushed` | True when the commit was on the upstream branch |
| `engine_version`, `lock_hash` | The engine version, and a hash of the versions of the packages it computes with (Polars, delta-rs, DuckDB, PyArrow) |
| `by` | The PC login name that promoted |
| `receipt`, `receipt_id` | The whole record as a [run receipt](notebooks.md) of kind `release`, and its SHA-256 id. Anyone with the row can recompute the id to check the record wasn't changed |

Rows written before this existed have these columns empty.

If the files can't be named by a pushed commit (uncommitted changes, unpushed commits, no upstream branch, or no Git), the promote still goes ahead and the promote card says so. To require a pushed commit instead, set `require_commit = true` for that environment in `environments.toml`:

```toml
[prod]
require_commit = true
```

Promote to that environment is then blocked until the files are committed and pushed.

### Who can promote

*Preview.* Before a promote to Test or Prod writes anything, the promote card checks your workspace role in each workspace the target uses. If Fabric lists you as a **Viewer** there, it says so and names the workspace's Admins to ask, and the promote is blocked. Fabric would refuse those writes anyway, partway through. If your role may come through a group, or Fabric won't show the workspace's access list, the card shows a note instead and Fabric decides when the promote writes.

To have a second person release to Prod, set `separate_promoter = true` for Prod in `environments.toml`:

```toml
[prod]
separate_promoter = true
```

Whoever promoted a metadata version to Test then can't also promote it to Prod. The check compares PC login names in the [release record](#release-records)'s `by` column. That makes it a team process check, not a security boundary: to make sure only release managers can change Prod, give only them Contributor or higher on the Prod workspaces.

### This PC or Fabric runs each environment

Each environment card has **Runs on: This PC | Fabric**, saved as `compute` in `environments.toml`. Fabric needs the tables in OneLake, so an environment with **This PC only** tables runs on the PC until you point it at a workspace.

With **Fabric** (Preview), **Run all** (and running one entity, backfills and rebuilds) uploads the metadata project and the app's engine to `LH_Meta/Files/_fabric_desktop/current`, and starts one notebook in the workspace, `nb_fabric_desktop_runner`. It's a Fabric Python notebook that runs the same engine against the same lakehouses. The upload is skipped when nothing changed. The app follows the job and shows the results when it finishes; the run log, checks and quarantine land in the same `LH_Meta` tables, so it doesn't matter where a run happened. A Fabric schedule runs every entity even when your PC is off, from the project as it was last uploaded.

## 8. Surrogate keys

Surrogate keys are issued in one place per environment: `LH_Keys`, in a dedicated Keys workspace (Dev keeps it on the PC). Each business key has its own key table, `sk.<name>`, with three columns: `<name>_sk` (INT), `<name>_bk` (the business key, unique) and `create_date`. Customer ABC gets one key, say 123, and every source that has Customer ABC maps to 123.

**Data mesh > Design > Surrogate keys:**

- **New key table** adds one, for example `customer`.
- **Pick Keys workspace** (or **Change**) sets where `LH_Keys` lives for the picked environment.
- The list shows each key table's key count, next key, and which tables use it. Select one to **look up a business key**, see its attributes, and copy the block that makes an entity use it.
- **Sync to LH_Keys** creates any missing key tables and writes the source system configuration.

An entity takes keys with a `[[keys]]` block. The first step of every run MERGEs business keys it hasn't seen into the key table (continuing from the highest key, never reusing one), then adds `<name>_sk` to every row. Rows with no business key get -1 (the Unknown member). A composite key is a list, stored as `"a||b"`. Entities that share a key table can run at the same time, on one PC or several: each MERGE is committed against the version of the key table its new keys were worked out from, and is redone if another run committed first, so no key goes to two business keys. A run checks the key table for a key or business key that appears twice and stops, writing nothing, if it finds one.

```toml
[[keys]]
table = "customer"
business_key = "customer_id"     # or ["source_code", "customer_id"]
```

### Keys in the platform

With a [platform](platform.md) in Fabric, an environment's keys come from `SQL_Platform` instead, one key system for every team. A project that already issued keys in `LH_Keys` moves them with **Move keys** on this tab: every key keeps its number, each row is checked, and only then does the environment switch (`keys = "platform"` in `environments.toml`).

In an environment whose keys are in the platform, the project doesn't make its own key tables. **Use a key table** picks one of the platform's (it still writes `metadata/keys/<name>.toml`, so the project says which keys it uses). Only platform admins add key tables: **Request a new one** sends a request to **Platform > Requests**, and the key table shows in the list once it's approved. A run that needs a key table the platform doesn't have stops and says to request it; **Sync** lists any that are missing.

## 9. Source systems and the variable library

**Data mesh > Design > Source systems** keeps every source's fixed id, code (like `CRM`), name, type, owner and active flag, and its connection in Dev, Test and Prod: server, port, database, schema, base URL, file path, authentication, tenant, username, Key Vault URL and secret name, Fabric connection id, gateway and extra options. Switch environment with the Dev / Test / Prod buttons above the connection fields.

**Passwords and keys never go here.** Give the Key Vault URL and the secret's name; the notebook reads the secret when it runs.

**Sync to LH_Keys** writes them to `config.source_system` and `config.source_connection`, one row per source per environment, so notebooks and pipelines can look up connection values by code.

**Variable library VL_Sources** (on the same tab) is one Fabric variable library per environment workspace, with that environment's values active. **Publish to Dev / Test / Prod** creates or updates it; promote updates it in the target. Notebooks read the same names in Fabric and on your PC:

```python
lib = notebookutils.variableLibrary.getLibrary("VL_Sources")
lib.CRM_server_host
```

## 10. Semantic model (Preview)

**Data mesh > Deploy > Semantic model** builds a Direct Lake semantic model over a layer's tables (the last layer, usually Gold) in Test or Prod. It reads the layer's lakehouse through its SQL analytics endpoint, with no import and no refresh schedule.

- Each entity is a table; dimensions and facts are marked. Facts get a row count measure.
- Relationships come from the metadata: a fact's `[[lookup]]` names the dimension and key, and any other column named like a dimension's surrogate key joins to it.
- Surrogate keys, `_run_id` and `_loaded_at` are hidden.
- **Create model** makes `SM_<Layer>` in the environment's workspace (rename it if you like). **Update model** updates it in place and keeps every lineage tag, so reports built on it keep working.

Dev on the PC has no Direct Lake model: switch the environment to Test or Prod.

## 11. Bring existing tables under management

To manage tables that already exist in a lakehouse, use **Adopt** on the lakehouse in the Estate X-ray on Home (Preview). It writes an entity for each table with `[source] table` pointing at the existing table and a contract from its columns. Review the files, then run them.

## 12. Run on a schedule or a server

- **On this PC or in Fabric:** **Schedule** at the top of Metadata, or **Schedules** on an environment card (Preview). Listed in **Monitor > Schedules**.
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
| `[contract]` | `keys` | The business key columns |
| | `drift` | This table's schema drift policy |
| `[contract.columns]` | `<column> = "<type>"` | `string`, `int64`, `float64`, `bool`, `date`, `datetime`, `decimal(p,s)` |
| `[[clean]]` | `column` plus `trim`, `case` (`lower`, `upper`, `title`), `replace` (`{ "old" = "new" }`), `null_if` (a list), `parse_date` (formats like `"dd/MM/yyyy"`), `fill_null`, `round` | Cleaning rules, applied in order |
| | `dedupe_keep` | `"newest:<column>"` or `"oldest:<column>"`: keep one row per key |
| `[[derive]]` | `column`, `expr` | A new column from a SQL expression |
| `[[check]]` | `column` with `not_null`, `unique`, `accepted_values` (a list), `range` (`{ min = 0, max = 100 }`) or `exists_in` (`"layer.table.column"`) | Column checks |
| | `name`, `expr` | A row rule as a SQL expression |
| | `python`, `owner`, `test` | A custom check |
| | `on_fail` | `stop`, `quarantine` or `warn` |
| `[[keys]]` | `table`, `business_key` | Surrogate keys from `LH_Keys` |
| `[[lookup]]` | `dim`, `on`, `as`, `at` | A dimension's surrogate key as of the fact's date |
| `[scd]` | `surrogate_key`, `tracked`, `type1` | Dimension settings |
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
| `keys` | `platform` (keys in `SQL_Platform`) or `lakehouse` (`LH_Keys`). Unset, keys come from the platform once the environment has one in Fabric, unless the project already has `LH_Keys` |
| `workspaces` | A workspace per designation, like `{ ingest = { id = "...", name = "Sales Ingest - Test" } }` |
| `require_commit` | `true`: promote here only from files a pushed Git commit holds ([Release records](#release-records)) |
| `separate_promoter` | `true`: whoever promoted this version to the environment before can't promote it here ([Who can promote](#who-can-promote)) |

Changing this file doesn't change the metadata version, so it never forces a re-promote.

### Data mesh files

`project.toml` (the project's domain and team), `metadata/products/<name>.toml` (data products) and `metadata/links/<alias>.toml` (links to other teams' products), plus an optional `[catalog]` block in an entity file. All are described in [Data mesh](data-mesh.md).

### Column lineage (Preview)

Fabric Desktop by KratosBI works out where every column comes from by reading the project's files, without running anything. Each column is one of: read from the source (by the same name, or through `rename`), a `[[derive]]` expression and the columns it uses, a `[[lookup]]` of a dimension's key, a `[[keys]]` surrogate key, or added by the framework. Each is labelled **declared** when a file states the link (a rename, an expression, a lookup, a key) or **inferred** when it is assumed from a matching name. Columns your Python adds (`[hooks] adds`, or every column of a `[step] custom`) are **opaque**: they're never guessed. Columns a hook runs after are flagged, because a hook can change any column. For one column, lineage follows the steps back to the landing files and finds every column built from it further on. The app's lineage view is still to come; the engine serves it as `meta_lineage`.

### Columns and tables the framework adds

| Where | What |
| --- | --- |
| Every row | `_run_id`, `_loaded_at` |
| Type 2 dimensions | `valid_from`, `valid_to`, `is_current` |
| Dimensions | The surrogate key column, and an Unknown member at -1 |
| `LH_Meta` | `meta.run_log`, `meta.dq_results`, `meta.watermarks`, `meta.schema_history`, `meta.deployments` |
| Each layer lakehouse | `quarantine.<schema>_<table>` for held-back rows |
| `LH_Keys` | `sk.<name>` per key table; `config.source_system`, `config.source_connection` |

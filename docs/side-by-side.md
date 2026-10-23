# Side by side: this PC vs Fabric

Side by side runs one notebook on your PC and in Fabric, one after the other, and shows how long each took and whether both wrote the same results. Use it to show what running locally saves, and to catch the "it works on my laptop" problem before a notebook goes to a schedule in Fabric.

!!! note "Preview"
    Side by side is built to Fabric's documented APIs but hasn't been tried on a live tenant yet. It is tagged **Preview** in the app, and **Settings > Preview features** can hide it.

## Turn it on

1. Open a notebook and attach a lakehouse (**Add data items**). Both runs read and write that lakehouse's real tables.
2. On the ribbon's **Home** or **Run** tab, select **Side by side**. It shows **Side by side: On**. The toggle is remembered for this notebook on this PC.
3. Select **Run all**.

With the toggle off, Run all works as before. You can also find it in the command bar (Ctrl+K): type *side by side*.

The notebook must be able to run in both places. The check above the cells says when it can't, and why: for example a path on your PC (`C:\data\...`), which Fabric can't reach, or a Scala cell, which your PC can't run.

## What happens

Run all goes through three steps, shown in a panel above the cells:

1. **Run on this PC.** The kernel restarts, so the run starts clean as Fabric's does, and every code cell runs in order. Cell output shows in the notebook as usual. If a cell fails, the run on this PC stops there, and the comparison still goes on to Fabric, so you see which side breaks. The result shows this PC's error under the timings; when both sides fail, fix that one first, since the same code usually fails in Fabric for the same reason (Fabric's own message, such as *System cancelled the Spark session due to statement execution failures*, rarely says why).
2. **Run in Fabric.** The notebook is published to its workspace (the same as **Publish to OneLake**) and started with Fabric's job scheduler, as **Run in Fabric** does. It shows in **Monitor** and in Fabric's Monitoring hub. The published copy gets two extra cells, one first and one last, that time the notebook's own cells inside Fabric. They aren't added to your notebook on this PC, and the next ordinary publish removes them.
3. **Check the outputs.** Every table either run wrote is compared.

Before a notebook that writes tables runs, KratosForge lists the tables it writes (read from the code) and asks you to confirm, because both runs replace what's in them. If a cell does something reading the code can't follow (`exec`, `%run`, `import *`), it says so and asks too. **Use Prove instead** runs both sides from the same table versions with their writes kept in a sandbox, so your tables don't change; see [Prove this notebook](notebooks.md#prove-this-notebook-preview).

The runs go one after the other, not at the same moment, so they never write the same table at once. You can leave the page: the bell tells you when the comparison is ready, and the last comparison shows again when you reopen the notebook. **Cancel** stops whichever run is going.

## Reading the result

| Part | What it shows |
| --- | --- |
| The top line | **Same results in both places**, **Results differ in N tables**, or which side failed |
| This PC | Time from the first cell to the last, and the PC's cores and memory. No Fabric job starts; its OneLake reads and writes still use some capacity, which isn't measured. |
| Fabric | Time from Run all to the end of the Fabric job, split into the **session start** (Fabric starting a session and queueing) and the **run** (the notebook's cells). Below it, the estimated CU-seconds and cost. |
| Faster | Which side was faster end to end, and on run time alone with Fabric's session start left out. Left out when either side failed, since a run that stopped early is only shorter. |
| Tables | Each table written: rows on each side, whether the columns match, and the result |

A table's result is one of:

| Result | Meaning |
| --- | --- |
| Match | Same rows, columns and checksum |
| Differs | Different rows, or a different column or value. The **Columns** cell names columns that are only on one side or whose types differ. |
| This PC only, Fabric only | Only one run wrote the table. Usually a cell that failed on the other side, or code that writes different tables depending on where it runs. |
| Couldn't read | The table couldn't be read to check it; the note says why |

### How tables are checked

KratosForge notes the Delta version of every table in the lakehouse before the PC run, after it, and after the Fabric run. A table whose version moved was written by that run, and Delta keeps each version, so both outputs can be read afterwards exactly as each run left them.

For each table on each side it computes:

- **Rows**: the row count.
- **Columns**: names and kinds (integer, number, string, date, timestamp, boolean). Kinds, not exact types, so an `int` from one engine and a `bigint` from another count as the same.
- **Checksum**: a hash of every row, added up so row order doesn't matter. Before hashing, numbers with a fraction (doubles and decimals) are rounded to 10 significant digits, because two engines can add up the same values in a different order and differ in the last digit. Timestamps are compared in UTC.

When a run **overwrites** a table, the whole table is compared. When a run only **appends**, the rows that run added are compared, because the second run's table also holds the first run's rows. When a run changes rows **in place** (MERGE, UPDATE, DELETE), the whole table after each run is compared; keep in mind that the Fabric run starts from what the PC run left.

Write outputs with `mode="overwrite"` (the default of `lakehouse.write_table`) for the cleanest comparison.

### Time and cost

The time on this PC runs from the start of the first cell to the end of the last. The Fabric time runs from Run all to the end of the job, so it includes publishing, queueing and starting a session: what someone waiting for the result sees. The session-start and run split comes from the timing cells; if they couldn't save their file, only the job time is shown.

Cost is an estimate: the Fabric job's time multiplied by the CUs of a Python notebook (or a Spark notebook, for a notebook that uses Spark) and the price per CU hour. Change those numbers in **Optimize > Cost and savings**. The Capacity Metrics app in Fabric has the billed numbers.

## The TPC-H benchmark

To test at scale with a standard workload, use the two TPC-H notebooks. TPC-H is the industry's standard decision-support benchmark: eight tables of orders, customers, parts and suppliers, and 22 queries.

Add them from the Side by side panel (**Add the TPC-H benchmark notebooks**) or the command bar (Ctrl+K, *TPC-H*). They are also in the repository, to download or share: [examples/benchmark](https://github.com/KratosDataGod/KratosForge/tree/main/examples/benchmark). To use a downloaded copy, put the `.ipynb` files in `%LOCALAPPDATA%\FabricDesktop\notebooks` and they show in the app.

### 1. Make the data: TPC-H data

Generates the TPC-H tables with DuckDB's tpch extension and writes them to the attached lakehouse as Delta tables in the `tpch` schema (in a lakehouse created without schemas, tables named `tpch_region`, `tpch_nation` and so on). Set `scale_factor` in the first cell:

| scale_factor | Order lines | Size (about) | Good for |
| --- | --- | --- | --- |
| 0.1 | 600 thousand | 100 MB | Trying it out |
| 1 | 6 million | 1 GB | The default |
| 10 | 60 million | 10 GB | A real load |
| 30 | 180 million | 30 GB | A PC with 64 GB of memory or more |

Run it once, on this PC (Run all with Side by side off) or in Fabric. Nothing is in the repository but the code: the data is generated when you run it, at any size. The generator writes through a database file in a temporary folder, so a scale factor bigger than your memory still works, and writes each table in slices of `batch_rows`.

The extension comes from DuckDB's extension site; if your network blocks it, the notebook installs the same extension from PyPI (`duckdb-extension-tpch`).

### 2. Run the benchmark: TPC-H benchmark

Loads the eight tables, runs the 22 queries and writes each answer to `bench.q01` ... `bench.q22` (`bench_q01` ... in a lakehouse without schemas). Turn on **Side by side** and select **Run all**.

The notebook times each step and shows a table at the end:

| Step | What it times |
| --- | --- |
| load | Reading the tables from OneLake into memory |
| Q01 ... Q22 | Each query, with the data already in memory |
| write answers | Writing the 22 answer tables to OneLake |

The PC run's table shows in the notebook; the Fabric run's is in its output in Fabric (**Open the run in Fabric**). Side by side then checks the 22 answers match.

Change `queries` in the first cell to run some queries only, for example `queries = [1, 6, 18]`.

To compare a write-heavy load instead, run **TPC-H data** itself side by side: both runs generate the same tables (the generator is deterministic), so the check confirms Fabric and this PC wrote identical data, and the times compare generating and writing gigabytes to OneLake.

### What to expect

On a PC, the time goes to reading the tables over the network and to computing; in Fabric, to starting the session and to computing on the notebook's cores. A Fabric Python notebook starts with 2 vCores and 16 GB of memory, so a scale factor that needs more memory than that can fail in Fabric while it works on a bigger PC, which is the kind of difference Side by side is for. Larger scale factors in Fabric need a bigger session (`%%configure` in a Python notebook) or a Spark notebook.

These runs follow TPC-H's data and queries but are not audited TPC-H results, and shouldn't be published as such. TPC-H is a trademark of the Transaction Processing Performance Council.

## Limits

- Both runs use the same lakehouse, so anything else writing to it during the comparison (another notebook, a schedule) shows up as a difference. Run comparisons in a lakehouse nobody else is writing to.
- The comparison doesn't look at files under `Files/`, only tables.
- Before, between and after the runs, KratosForge reads the version of every table in the lakehouse. In a lakehouse with hundreds of tables that adds a wait (it isn't counted in either run's time); a lakehouse just for benchmarks keeps it short.
- Cell output from the Fabric run stays in Fabric; the panel links to it.
- Only notebooks can run side by side. Metadata runs load incrementally (the second run would find nothing new to load), so they aren't compared this way.
- While a comparison runs, the notebook's kernel is busy, so its cells can't be run one by one until it finishes or is cancelled.

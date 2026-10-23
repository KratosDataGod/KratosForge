# Run the last cell on Fabric (Preview)

Run a notebook on this PC, then run its last cell on Fabric Spark. The usual reason is V-Order: tables written from this PC don't have it, and Direct Lake reads V-Ordered tables faster. Only Fabric Spark writes V-Order, so the last cell is where `OPTIMIZE ... VORDER` goes.

The cells above it run on this PC as always. That starts no Fabric job, but their OneLake reads and writes use capacity. The last cell is a Spark job on the lakehouse's workspace capacity, so it uses capacity units, and the app asks before it starts.

## Mark the last cell

On the last code cell's **⋯** menu, pick **Run on Fabric (Spark)**. The cell shows a **Runs on Fabric · Spark** badge.

- Only the last code cell can carry the mark. Markdown and empty cells below it don't count.
- If a code cell is added below the marked one, **Run all** stops before any cell runs and asks you to move the mark.
- **Run on this PC, not Fabric**, on the same menu, clears it.
- The mark is saved in the notebook (cell metadata `fabric_desktop.run_on = "fabric"`).

## Insert V-Order cell

On the marked cell's **⋯** menu, **Insert V-Order cell** lists the attached lakehouse's tables. The tables the notebook's other cells name are ticked. **Insert** replaces the cell's code with one `OPTIMIZE ... VORDER` per ticked table:

```python
# V-Order the tables this notebook wrote, so Direct Lake reads them faster.
spark.sql("OPTIMIZE `gold`.`fact_sales` VORDER")
spark.sql("OPTIMIZE `gold`.`dim_customer` VORDER")
```

You can write any Spark code in the cell instead. V-Order is turned on for the step's Spark session (`spark.sql.parquet.vorder.default`), so anything the cell writes is V-Ordered too. The cell runs in Fabric, not in this PC's kernel, so it can read tables but not the Python variables of the cells above it. `%%sql` cells become `spark.sql`; other magics don't run there.

## Run it

**Run all** runs every other cell on this PC. If one fails, the run stops and nothing is sent to Fabric. Then, for the marked cell, or when you run that cell on its own:

1. The [risk check](finops/risk-check.md) runs, as for **Run in Fabric**. A blocked check stops here.
2. A confirm shows the workspace, lakehouse, the tables it V-Orders, and what it writes. **Stop here** sends nothing.
3. The cell is published as a one-cell Spark notebook, **<notebook name> (Fabric step)**, in the lakehouse's workspace, with the same lakehouse attached, and started as a Fabric job.
4. The cell shows Queued, then Running, with a link to the run in Fabric and **Cancel**. The run is in **Monitor > Runs** too.
5. When it ends, the cell shows what it printed, or its error, and each table it OPTIMIZEd: **V-Ordered** when every file's Parquet footer carries Fabric's V-Order mark. The app reads the footers of the newest 200 files. For a table with more files than that, it says how many it checked.

Scheduled runs on this PC, local pipeline runs, `%run` and `notebookutils.notebook.run` skip the marked cell; a schedule says so in its run. None of them starts a Fabric job. **Prove** and **Side by side** refuse a notebook with the mark, since their run on this PC can't run it: clear the mark to compare the notebook.

## If a table isn't V-Ordered

`OPTIMIZE` rewrites only the files it compacts ([Table compaction](https://learn.microsoft.com/en-us/fabric/data-engineering/table-compaction)). A file it has nothing to compact with stays as it is: the only file of a small table written in one go, or files that are already large. Raising `spark.databricks.delta.optimize.minFileSize` doesn't change that for a table of one file (tested on Fabric, October 2026).

Then the table shows **not V-Ordered** and a **Rewrite all files** button. The button changes the cell so it reads each table and writes it back, which is an overwrite with no data change (`dataChange` false), V-Ordered like everything the step writes. Run the cell again. That rewrites the whole table, so it takes longer and uses more capacity, and it adds a Delta version whose old files stay until VACUUM. V-Order is asked for on the write itself and in the session, which Fabric applies even when the table's own `delta.parquet.vorder.enabled` is false ([V-Order settings](https://learn.microsoft.com/en-us/fabric/data-engineering/delta-optimization-and-v-order)). A table with a `GENERATED ALWAYS AS IDENTITY` column can't be written back this way: the step fails with Spark's error and the table is left as it was.

## How this works

| | |
| --- | --- |
| What it reads | The marked cell's code and the attached lakehouse's table list; after the run, the job's status, the output file, and each OPTIMIZEd table's Delta log and Parquet footers. |
| What it changes | In the workspace: creates or replaces the **<name> (Fabric step)** notebook item. In each OPTIMIZEd or rewritten table: a new Delta version with rewritten files; old files stay until VACUUM. Anything else the cell's own code writes. |
| Where results go | `Files/_fabric_desktop/results/<run>.json` in the attached lakehouse (what the cell printed, read back into the cell), and one line in this PC's Fabric runs log, shown in Monitor > Runs. |

The **<name> (Fabric step)** notebook is replaced on every run, so edits made to it in Fabric are lost. A notebook in a folder is named without its folder, so two notebooks with the same name in different folders share one step notebook in a workspace.

## What V-Order is

V-Order is a write-time Parquet layout. Fabric Spark applies it when it writes; in workspaces created recently it is off by default, which is why the step turns it on. A file keeps the layout it was written with until something rewrites it. See [Delta Lake table optimization and V-Order](https://learn.microsoft.com/en-us/fabric/data-engineering/delta-optimization-and-v-order) and, for Direct Lake, [Table maintenance and optimization](https://learn.microsoft.com/en-us/fabric/fundamentals/table-maintenance-optimization).

For metadata project tables, Data mesh **Table health** runs Fabric's table maintenance job with V-Order instead ([metadata-guide.md](metadata-guide.md)).

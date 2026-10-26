# Validate a notebook (Preview)

**Validate** checks that a notebook works from the top, the way Fabric, a schedule or a pipeline runs it, and helps you clean up its code. Open a notebook and select **Validate** on the **Home** or **Run** tab. A panel opens beside the cells with two tabs: **Problems** and **Clean up**.

## Problems

### Code checks

As soon as the panel opens, Validate reads the code without running anything and lists what it finds. Each finding has a cell, an explanation and, where there is one, the fix. **Go to cell** jumps to the cell.

| Severity | Examples |
| --- | --- |
| **Error** | A cell doesn't parse. A cell uses a name defined in a later cell (it only works now because the kernel kept it from an earlier run). A secret written in the code (shown masked). A package this PC doesn't have, when the notebook runs on this PC. Cells that read tables when no lakehouse is attached. |
| **Warning** | A name no cell defines (a typo or a missing import). A cell that can't run in the place it isn't set to run. `%pip`. Reading the clock or random values. Network calls, files written on this PC, or another notebook run. More than one parameter cell. |
| **Info** | The parameter cell and the names a pipeline can pass. Frozen cells. The tables the notebook reads and writes. A function that uses a name defined in a later cell (that works, as long as nothing calls the function first). |

The headline says **Ready**, **Ready with warnings**, **Not ready** or, before a clean run, **Code checks**. The chips filter the list by severity.

### Clean run

**Run clean check** starts a fresh kernel on this PC, with none of your variables, and runs every code cell from the top, stopping at the first failure. Frozen cells don't run. A failure is matched to the code check that predicted it, so "used before it was defined" shows once, marked as found both ways.

What a clean run touches:

- **Tables** are read as they are when it starts. Table writes go to a sandbox folder in the attached lakehouse, `Files/_fabric_desktop/proofs/validate-<id>/pc/`, never to the lakehouse's own tables, and that folder is deleted when the run ends.
- **Your notebook's kernel** and its variables are left alone. The clean run has its own process, with the same time and memory limits.
- **No Fabric job starts.** Reading and writing OneLake still uses some capacity.
- **What it can't contain**, such as network calls or files written on this PC, really happens, so Validate lists those cells and asks before it starts. A write it can't redirect (a direct `deltalake` write or `notebookutils.fs` change) stops it before it starts.
- **Spark cells** run on this PC's PySpark-compatible engine (DuckDB), not Fabric's Spark. `spark.read.table` and `spark.sql` read the same tables as other cells, and `saveAsTable` writes go to the same sandbox. Spark features this PC doesn't have (RDDs, UDFs, continuous or Kafka streams, `MERGE`/`UPDATE`/`DELETE` through `spark.sql`, the `replaceWhere` and `partitionOverwriteMode` write options) are listed by the code checks, and the run stops at the first one. Results can differ slightly from Fabric's Spark; Side by side compares the two. Spark `parquet()` or `csv()` writes to a path outside the lakehouse are listed with the other files written on this PC.

When the code changes after a check or a clean run, the panel says the result is **Out of date** and which cells changed. **Validate again** runs a new clean run. **Save report** writes the result as a new Markdown file in Downloads. The last 100 results are kept on this PC.

## Clean up

Clean up reads the code and lists two kinds of changes. Nothing runs.

**Tidy-ups** are changes you tick and apply together:

| Tidy-up | What it changes | Ticked at first |
| --- | --- | --- |
| Remove unused imports | An import of a standard-library module, or of numpy, pandas, polars, pyarrow, duckdb, deltalake, requests or scipy, that no cell uses | Yes |
| Remove imports no cell names | An import of any other package that no cell names | No: some packages add things when imported (pyjanitor adds DataFrame methods), so check first |
| Remove repeated imports | An import an earlier cell already made, with nothing rebinding the name in between | Yes |
| Tidy spacing | Spaces at line ends and runs of more than two blank lines, outside multi-line strings | Yes |
| Remove empty cells | Cells with no code (never the parameter cell) | Yes |
| Remove print lines | One-line `print(...)` statements | No: you may want them |

The tidy-ups ticked at first don't change what the code does. Tidy-ups leave frozen cells alone, skips imports when the notebook can make names at run time (`exec`, `import *`, `%run`) or the line has a comment, keeps spacing changes only when the code is identical after them, and drops any change after which a cell wouldn't parse. **Apply** changes the cells, and the notebook saves as it does after any edit. **Undo** puts the cells back as they were, as long as you haven't edited them since; after an edit, Undo isn't offered, so it can't lose that edit.

**Suggestions** point at a cell and leave the change to you:

- **A table read again** by a later cell, with no write to it in between. Each read loads the table from OneLake again, which uses capacity.
- **A whole table turned into pandas** (`read_table(...).to_pandas()`), which copies every row into memory before anything filters it.
- **A row-by-row loop** (`iterrows`, `itertuples`).
- **A parameter's value written out again**, so a pipeline that passes another value won't change it.

## For external AI tools

The `fabric_desktop_validate_notebook` capability returns the same findings and verdict, and the `review_notebook` prompt reports errors first. See [External AI tools](ai/workflows.md).

## Limits

- Code checks read the code; they can't see names or calls built at run time.
- A clean run uses this PC's packages. Spark cells run on this PC's PySpark-compatible engine (DuckDB), not Fabric's Spark: what it doesn't have stops the run, and results can differ slightly.
- Outside the lakehouse's tables, a clean run does what the code does (files on this PC, network calls); the ones the code checks find are listed before it starts.

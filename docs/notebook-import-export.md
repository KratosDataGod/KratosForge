# Fabric and Databricks notebook formats (Preview)

KratosForge reads and writes notebooks in the formats Fabric, Databricks and Jupyter tools use, so you can bring work in from another tool and hand a notebook to someone who doesn't use KratosForge. This is in Preview: the Fabric and Databricks formats follow what those products write, but haven't yet been checked against every version of a live workspace.

## Export

Open a notebook and select **Share > Export** on the **Home** tab, or search for "Export notebook" in the command bar (Ctrl+K).

| Choice | What you get |
| --- | --- |
| **Jupyter notebook (.ipynb)** | The cells and their settings (parameter cell, hidden input or output, frozen), with no outputs. The same file KratosForge saves. |
| **Jupyter notebook with outputs (.ipynb)** | As above, plus each code cell's outputs as they are on screen now and its run number: text, errors with their traceback, tables (as text and an HTML table), charts and HTML output. |
| **Fabric source (.py)** | The `notebook-content.py` format a Git-connected Fabric workspace stores: `# Fabric notebook source`, a `# CELL ********************` line before each code cell (`# PARAMETERS CELL ********************` for the parameter cell), `# MARKDOWN ********************` before each markdown cell with its lines starting `# `, and a `# METADATA ********************` block giving each cell's language. A SQL, Scala or R cell's lines start with `# MAGIC`. The attached lakehouse is written as the notebook's default lakehouse. |
| **Databricks source (.py)** | The file Databricks exports for a Python notebook: `# Databricks notebook source`, cells separated by `# COMMAND ----------`, markdown cells as `# MAGIC %md` lines and other languages as `# MAGIC %sql`, `# MAGIC %scala` or `# MAGIC %r`. A cell that starts with a line magic such as `%run`, `%fs` or `%pip` is written as `# MAGIC` lines too. Databricks has no parameter cell (it uses widgets), so that setting isn't carried. After saving, a dialog lists everything that [didn't carry over](#what-a-databricks-conversion-doesnt-carry-over), cell by cell. |
| **Web page (.html)** | A page anyone can open in a browser: each cell's code, then its outputs as on screen. Every piece of text is escaped, HTML outputs are left out, and the page can't run any script. |

Outputs are only what you see on screen: run the notebook first if you want them. Exporting doesn't save the notebook or change it.

### Where the file goes

The file is saved in your **Downloads** folder, named after the notebook (for example `Daily sales.py`). A file that is already there is never replaced: the new one gets a number, such as `Daily sales (2).py`. The message after an export gives the full path. KratosForge doesn't ask where to save yet; move the file from Downloads to wherever you need it.

## Import Fabric and Databricks notebooks

Select **Import from Fabric or Databricks** under **Start something** on **Home**, or search for "Import Fabric or Databricks" in the command bar, and pick a file.

| File | How it's read |
| --- | --- |
| `.ipynb` | A Jupyter notebook from Fabric, Databricks, VS Code or Jupyter. Cells and their settings come in; outputs don't. |
| `.py` starting `# Fabric notebook source` | Fabric source, as above. A cell whose metadata says it is Spark SQL, Scala or R gets `%%sql`, `%%spark` or `%%sparkr`. |
| `.py` starting `# Databricks notebook source` | Databricks source. `%md` cells become markdown, `%sql`, `%scala` and `%r` become `%%sql`, `%%spark` and `%%sparkr` cells, `%python` cells become plain Python, and `%run`, `%pip` and other line magics are kept as they are. `# DBTITLE` lines are dropped. |
| `.sql` starting `-- Databricks notebook source` | A Databricks SQL notebook: each cell becomes a `%%sql` cell, except `%md` and other magic cells. |
| `.scala` starting `// Databricks notebook source` | A Databricks Scala notebook: each cell becomes a `%%spark` cell. |
| `.py` with `# %%` lines | A percent-format script, as VS Code, Spyder and Jupytext write. `# %% [markdown]` cells become markdown. A Jupytext header at the top is skipped. |
| Any other `.py` or `.sql` | One code cell with the whole file (a `.sql` file gets `%%sql`). |
| `.dbc` | A Databricks archive: every notebook in it, each as its own new notebook. The dialog shows the folder each came from. |

Before anything is written, the dialog shows the format it found and each notebook it will create, with its name, number of cells and languages. For a Databricks notebook it also lists, cell by cell, everything the conversion dropped or changed and anything that won't run the same on this PC (see below). Select **Import** to create them. The first new notebook opens; all of them are in **Recent notebooks**.

Limits: a file can be up to 64 MB. A `.dbc` archive can hold up to 500 files, 16 MB per notebook and 128 MB in all once unpacked, and up to 200 notebooks. An archive with a path that climbs out of it (`..`, a drive letter or a leading slash) or that is encrypted isn't imported at all, and neither is a damaged one or one whose entries hold more than their headers say; the message says which.

## What a Databricks conversion doesn't carry over

Every Databricks import (source files and `.dbc` archives) and every export as Databricks source lists what didn't carry over as it was, with the cell it's in. Each entry is one of:

- **Not carried over:** it isn't in the new notebook at all.
- **Changed:** it's there, in another form.
- **Check before running:** it's there as written, but won't work the same where the notebook now runs.
- **Before running in Fabric:** it runs on this PC as written, but needs a change before the notebook runs in Fabric.

**Importing from Databricks**

| In Databricks | In KratosForge | Listed as |
| --- | --- | --- |
| Cells, markdown (`%md`), Python (`%python`) | The same | Not listed |
| `%sql`, `%scala`, `%r`, `%sh` cells | `%%sql`, `%%spark`, `%%sparkr`, `%%sh` cells | Changed |
| `%md-sandbox` | A markdown cell; its HTML and styles may look different | Changed |
| `%run`, `%fs`, `%pip` and other line magics | Kept as written; they run on this PC | Not listed, except `%run` with a workspace path (`./`, `../` or `/`), which runs the notebook of that name from this PC's notebooks folder: import that notebook too (Check) |
| `dbutils.notebook`, `dbutils.secrets`, `dbutils.jobs.taskValues`, `dbutils.fs` | Kept; they run on this PC ([dbutils on this PC](notebooks.md)) | Not listed |
| `dbutils.widgets` | Kept; it runs on this PC. Fabric has no widgets: put the defaults in a parameter cell, and a pipeline or `notebookutils.notebook.run` passes the values | Before running in Fabric |
| `dbutils.library.restartPython()` | Kept; it runs on this PC. In Fabric the call is `notebookutils.session.restartPython()`, and scheduled runs should get their packages from the Fabric environment | Before running in Fabric |
| `dbutils.fs.mount` and other mount calls, `dbutils.library.install`, `dbutils.credentials` | Kept, but there's no equivalent on this PC | Check |
| `dbfs:/`, `/dbfs/`, `/Volumes/` and `/Workspace/` paths | Kept, but neither this PC nor Fabric can reach Databricks storage: use `Files/` or `Tables/` in a lakehouse | Check |
| Delta Live Tables: `import dlt`, `@dlt.` decorators, `CREATE OR REFRESH`, `CREATE STREAMING TABLE`, `CREATE LIVE TABLE`, `APPLY CHANGES INTO`, `EXPECT` constraints | Kept, but only a Databricks pipeline runs them. Rewrite each dataset as a cell that writes its table and run the cells from a pipeline | Check |
| `spark.databricks.` settings other than `spark.databricks.delta.` (for example `spark.databricks.io.cache.enabled`) | Kept, but Fabric doesn't have them: setting one does nothing there, and reading one fails unless the call gives a default. Delta's own settings work in Fabric and aren't listed | Before running in Fabric |
| Streams with `trigger(processingTime=...)` or `trigger(continuous=...)` | Kept; on this PC they run once, as `availableNow`. In Fabric a stream that keeps running uses capacity the whole time, and continuous triggers aren't supported: run `trigger(availableNow=True)` on a schedule | Before running in Fabric |
| Cell titles (`# DBTITLE`, or a `.dbc` cell's title) | Dropped | Not carried over |
| A `.dbc` cell's saved results, and its hidden code or hidden result setting | Dropped: imported notebooks start without outputs | Not carried over |
| A `.dbc` notebook's dashboards | Dropped | Not carried over |

The Fabric entries are based on the migration gotchas in Microsoft's [skills-for-fabric](https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/databricks-migration/resources/migration-gotchas.md). Some of its gotchas can't be seen in a notebook's code, so they aren't listed: how Unity Catalog names map (one schema-enabled lakehouse per catalog keeps `schema.table` as it was), Photon tuning, cluster init scripts, and the differences between Databricks Repos and Fabric's Git integration.

**Exporting as Databricks source**

| In KratosForge | In the Databricks file | Listed as |
| --- | --- | --- |
| `%%sql`, `%%spark`, `%%sparkr`, `%%sh` cells | `%sql`, `%scala`, `%r`, `%sh` | Changed |
| The parameter cell setting | Dropped: Databricks uses widgets (`dbutils.widgets.text` and `dbutils.widgets.get`) | Not carried over |
| Hidden code, hidden output and frozen settings | Dropped | Not carried over |
| The attached lakehouse, and outputs | Dropped: Databricks source holds code only | Not carried over (whole notebook) |
| Cell magics Databricks doesn't have, such as `%%configure`, `%%html` or `%%powershell` | Kept as written; they won't run there | Check |
| A cell that starts with a line magic and has Python after it | In Databricks the whole cell is the magic, so the Python won't run | Check |
| `%%md` code cells | `%md`, which Databricks shows as markdown, so the cell comes back from Databricks as a markdown cell | Changed |
| Options on a cell magic line, such as `%%sql -o result` | Dropped: the file has `%sql` on its own | Not carried over |

## Round trips

A notebook exported as Databricks source, Fabric source or `.ipynb` and imported again comes back with the same cells: as many, in the same order, each the same kind (code or markdown) in the same language, with the same text. Tests write 300 generated notebooks through each format and read them back, with Python, `%%sql`, `%%spark`, `%%sparkr`, `%%md`, `%run` and `%pip` cells, markdown, empty cells, parameter cells, non-English text, tabs, trailing spaces, Windows line endings, and text that looks like another format's cell markers.

To keep that, a Python cell that the source format would read back as something else is written in a form that format reads as the same code: in Databricks source, an empty cell, or one with a `# COMMAND ----------` or `# DBTITLE` line, or with only `# MAGIC` lines, is written as a `%python` cell; in Fabric source, a cell with a `# CELL`, `# PARAMETERS CELL`, `# MARKDOWN` or `# METADATA` marker line, or with only `# MAGIC` lines, is written as `# MAGIC` lines, with its metadata still saying Python. Trailing spaces in markdown and magic cells are kept.

What `.py` source can't hold, so it changes on the way through (`.ipynb` keeps all of these):

- **Line endings and blank lines at the edges of a cell.** The file uses `\n` line endings, and a cell's leading and trailing blank lines aren't kept.
- **A markdown line that is a Fabric marker without its `# `,** such as `CELL ********************`. Fabric writes markdown lines behind `# `, which turns that line into Fabric's own cell marker, so in Fabric source the cell splits there, as it would in Fabric. Databricks source keeps it.
- **A code cell that starts with a Databricks language magic** such as `%sql`, `%scala` or `%md` (one `%`). That is how Databricks marks a cell's language, so it comes back from Databricks source as a `%%sql`, `%%spark` or markdown cell.
- **Databricks source only:** the parameter cell setting, `%%md` cells and cell magic options, each listed when you export (see above), and `%%pyspark`, which comes back as plain Python, the same language.
| `notebookutils` or `mssparkutils` calls | Kept; Databricks has `dbutils` instead | Check |

## How this works

- **What it reads:** for export, the notebook's cells and outputs as they are on screen. For import, the file you pick: the app reads it on this PC and sends its contents to the local engine. A `.dbc` archive is read in memory; nothing in it is unpacked to disk.
- **What it writes:**
    - **Export** writes one new file in your Downloads folder. It never replaces a file, and it doesn't save or change the notebook.
    - **Import** writes one new notebook file (`.ipynb`) for each notebook in the picked file, in the app's own notebooks folder. Each name is reserved by creating the file, so a notebook that already has that name is never replaced: the new one gets the next free name, such as `Sales (2)`. The picked file is never changed.
- **What it doesn't do:** nothing is sent to Fabric or Databricks, and no notebook runs. To put an imported notebook in a Fabric workspace, open it and use **Publish to OneLake** as usual.

Each import and export is a line in the [audit trail](user-guide.md#audit-trail), with the file or notebook name; the contents aren't recorded. Checking a file before import isn't recorded.

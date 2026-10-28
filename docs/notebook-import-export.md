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

Limits: a file can be up to 64 MB. A `.dbc` archive can hold up to 500 files, 16 MB per notebook and 128 MB in all once unpacked, and up to 200 notebooks. An archive with a path that climbs out of it (`..`, a drive letter or a leading slash) or that is encrypted isn't imported at all.

## What a Databricks conversion doesn't carry over

Every Databricks import (source files and `.dbc` archives) and every export as Databricks source lists what didn't carry over as it was, with the cell it's in. Each entry is one of:

- **Not carried over:** it isn't in the new notebook at all.
- **Changed:** it's there, in another form.
- **Check before running:** it's there as written, but won't work the same where the notebook now runs.

**Importing from Databricks**

| In Databricks | In KratosForge | Listed as |
| --- | --- | --- |
| Cells, markdown (`%md`), Python (`%python`) | The same | Not listed |
| `%sql`, `%scala`, `%r`, `%sh` cells | `%%sql`, `%%spark`, `%%sparkr`, `%%sh` cells | Changed |
| `%md-sandbox` | A markdown cell; its HTML and styles may look different | Changed |
| `%run`, `%fs`, `%pip` and other line magics | Kept as written; they run on this PC | Not listed, except `%run` with a workspace path (`./`, `../` or `/`), which runs the notebook of that name from this PC's notebooks folder: import that notebook too (Check) |
| `dbutils.widgets`, `dbutils.notebook`, `dbutils.secrets`, `dbutils.jobs.taskValues`, `dbutils.fs` | Kept; they run on this PC ([dbutils on this PC](notebooks.md)) | Not listed |
| `dbutils.fs.mount` and other mount calls, `dbutils.library.install`, `dbutils.credentials` | Kept, but there's no equivalent on this PC | Check |
| `dbfs:/`, `/dbfs/`, `/Volumes/` and `/Workspace/` paths | Kept, but this PC can't reach Databricks storage | Check |
| Cell titles (`# DBTITLE`, or a `.dbc` cell's title) | Dropped | Not carried over |
| A `.dbc` cell's saved results, and its hidden code or hidden result setting | Dropped: imported notebooks start without outputs | Not carried over |
| A `.dbc` notebook's dashboards | Dropped | Not carried over |

**Exporting as Databricks source**

| In KratosForge | In the Databricks file | Listed as |
| --- | --- | --- |
| `%%sql`, `%%spark`, `%%sparkr`, `%%sh` cells | `%sql`, `%scala`, `%r`, `%sh` | Changed |
| The parameter cell setting | Dropped: Databricks uses widgets (`dbutils.widgets.text` and `dbutils.widgets.get`) | Not carried over |
| Hidden code, hidden output and frozen settings | Dropped | Not carried over |
| The attached lakehouse, and outputs | Dropped: Databricks source holds code only | Not carried over (whole notebook) |
| Cell magics Databricks doesn't have, such as `%%configure`, `%%html` or `%%powershell` | Kept as written; they won't run there | Check |
| A cell that starts with a line magic and has Python after it | In Databricks the whole cell is the magic, so the Python won't run | Check |
| `notebookutils` or `mssparkutils` calls | Kept; Databricks has `dbutils` instead | Check |

## How this works

- **What it reads:** for export, the notebook's cells and outputs as they are on screen. For import, the file you pick: the app reads it on this PC and sends its contents to the local engine. A `.dbc` archive is read in memory; nothing in it is unpacked to disk.
- **What it writes:**
    - **Export** writes one new file in your Downloads folder. It never replaces a file, and it doesn't save or change the notebook.
    - **Import** writes one new notebook file (`.ipynb`) for each notebook in the picked file, in the app's own notebooks folder. Each name is reserved by creating the file, so a notebook that already has that name is never replaced: the new one gets the next free name, such as `Sales (2)`. The picked file is never changed.
- **What it doesn't do:** nothing is sent to Fabric or Databricks, and no notebook runs. To put an imported notebook in a Fabric workspace, open it and use **Publish to OneLake** as usual.

Each import and export is a line in the [audit trail](user-guide.md#audit-trail), with the file or notebook name; the contents aren't recorded. Checking a file before import isn't recorded.

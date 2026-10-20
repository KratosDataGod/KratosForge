# Import and export notebooks (Preview)

KratosForge reads and writes notebooks in the formats Fabric, Databricks and Jupyter tools use, so you can bring work in from another tool and hand a notebook to someone who doesn't use KratosForge. This is in Preview: the Fabric and Databricks formats follow what those products write, but haven't yet been checked against every version of a live workspace.

## Export

Open a notebook and select **Export** on the **Home** tab, or search for "Export notebook" in the command bar (Ctrl+K).

| Choice | What you get |
| --- | --- |
| **Jupyter notebook (.ipynb)** | The cells and their settings (parameter cell, hidden input or output, frozen), with no outputs. The same file KratosForge saves. |
| **Jupyter notebook with outputs (.ipynb)** | As above, plus each code cell's outputs as they are on screen now and its run number: text, errors with their traceback, tables (as text and an HTML table), charts and HTML output. |
| **Fabric source (.py)** | The `notebook-content.py` format a Git-connected Fabric workspace stores: `# Fabric notebook source`, a `# CELL ********************` line before each code cell (`# PARAMETERS CELL ********************` for the parameter cell), `# MARKDOWN ********************` before each markdown cell with its lines starting `# `, and a `# METADATA ********************` block giving each cell's language. A SQL, Scala or R cell's lines start with `# MAGIC`. The attached lakehouse is written as the notebook's default lakehouse. |
| **Databricks source (.py)** | The file Databricks exports for a Python notebook: `# Databricks notebook source`, cells separated by `# COMMAND ----------`, markdown cells as `# MAGIC %md` lines and other languages as `# MAGIC %sql`, `# MAGIC %scala` or `# MAGIC %r`. Databricks has no parameter cell (it uses widgets), so that setting isn't carried. |
| **Web page (.html)** | A page anyone can open in a browser: each cell's code, then its outputs as on screen. Every piece of text is escaped, HTML outputs are left out, and the page can't run any script. |

Outputs are only what you see on screen: run the notebook first if you want them. Exporting doesn't save the notebook or change it.

### Where the file goes

The file is saved in your **Downloads** folder, named after the notebook (for example `Daily sales.py`). A file that is already there is never replaced: the new one gets a number, such as `Daily sales (2).py`. The message after an export gives the full path. KratosForge doesn't ask where to save yet; move the file from Downloads to wherever you need it.

## Import

Select **Import notebook** under **Start something** on **Home**, or search for "Import a notebook file" in the command bar, and pick a file.

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

Before anything is written, the dialog shows the format it found and each notebook it will create, with its name, number of cells and languages. Select **Import** to create them. The first new notebook opens; all of them are in **Recent notebooks**.

Limits: a file can be up to 64 MB. A `.dbc` archive can hold up to 500 files, 16 MB per notebook and 128 MB in all once unpacked, and up to 200 notebooks. An archive with a path that climbs out of it (`..`, a drive letter or a leading slash) or that is encrypted isn't imported at all.

## How this works

- **What it reads:** for export, the notebook's cells and outputs as they are on screen. For import, the file you pick: the app reads it on this PC and sends its contents to the local engine. A `.dbc` archive is read in memory; nothing in it is unpacked to disk.
- **What it writes:**
    - **Export** writes one new file in your Downloads folder. It never replaces a file, and it doesn't save or change the notebook.
    - **Import** writes one new notebook file (`.ipynb`) for each notebook in the picked file, in the app's own notebooks folder. Each name is reserved by creating the file, so a notebook that already has that name is never replaced: the new one gets the next free name, such as `Sales (2)`. The picked file is never changed.
- **What it doesn't do:** nothing is sent to Fabric or Databricks, and no notebook runs. To put an imported notebook in a Fabric workspace, open it and use **Publish to OneLake** as usual.

Each import and export is a line in the [audit trail](user-guide.md#audit-trail), with the file or notebook name; the contents aren't recorded. Checking a file before import isn't recorded.

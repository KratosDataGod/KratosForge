# Notebook and SQL editor parity

Chris asked on 2026-11-13 for the notebook and SQL editor to have the same functionality as the major
notebooks and SQL editors: a feature parity matrix against the top ten, a roadmap, and an execution
plan ordered by capability and usefulness, reaching 95% as soon as possible and the last 5% soon after.

This page is that plan. It is read against main `e9e8e3e8b` (2026-11-13).

## How to read it

- **Comparison set.** Notebooks: JupyterLab 4, VS Code notebooks (Jupyter extension), Fabric
  notebooks, Databricks notebooks, Hex. SQL editors: SSMS 21/22, Azure Data Studio and its successor
  the VS Code MSSQL extension (Azure Data Studio was retired in February 2026), DataGrip, DBeaver,
  Snowflake Snowsight.
- **Competitor columns** come from each product's public documentation and common use as of
  November 2026. They were not re-read or re-tested for this page, and products change fast (Azure Data
  Studio was retired this year), so a dated correction to any cell is welcome. **Y** has it, **P** has part of it (or only
  through a common extension), **N** doesn't.
- **KratosForge** cells were checked in this repository's code at the commit above, with the file
  that shows it. **Have** = on main, **Partial** = some of it (the note says what is missing),
  **Gap** = missing. Preview features count as Have; they stay tagged Preview until tested.
- **Counted rows.** A capability is in the parity count when at least two of the five products in its
  group have it (Y or P). Rows that can't run on a PC (real-time co-editing, item sharing, R and Scala
  kernels) and the in-editor AI assistant (by policy KratosForge is AI-ready, not AI-dependent: AI tools
  reach it through the local API in [docs/ai](../ai/README.md)) are listed but not counted.
- **Score** = (Have + half of Partial) / counted rows.
- Earlier, deeper comparisons against Fabric and Databricks alone are in the notebook parity files
  (2026-10-20 program, about 96% of the rows that can run locally done) and the Azure SQL query editor
  check (2026-11-04). This page widens the set to ten products and adds the SQL editor.

## Where we stand

| Group | Counted rows | Have | Partial | Gap | Score |
| --- | --- | --- | --- | --- | --- |
| Notebook | 54 | 40 | 3 | 11 | 77% |
| SQL editor | 45 | 19 | 9 | 17 | 52% |
| **Both** | **99** | **59** | **12** | **28** | **66%** |

The notebook is close: its gaps are Markdown rendering (math, tables, images), code navigation and a
few comfort features. The SQL editor's editing is strong (dialects, schema-aware suggestions, Format
SQL and a compatibility check that none of the others have), but its **results grid** is a plain table
(no sort, filter, column resize or value viewer) and it runs **one statement at a time**. Those two
areas are most of the gap and most of what developers notice in the first hour.

## Notebook matrix

Products: JL = JupyterLab, VSC = VS Code, Fab = Fabric, Dbx = Databricks.

| # | Capability | JL | VSC | Fab | Dbx | Hex | KratosForge | Where / what's missing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| N01 | Code and Markdown cells; add, delete, move, drag to reorder | Y | Y | Y | Y | Y | Have | `app/ui/notebook.js`, `nbdrag.js` |
| N02 | Command-mode keys (A, B, DD, M, Y, Z, C, X, V, Shift+arrows) | Y | Y | Y | Y | P | Have | `notebook.js`, `nbselect.js` |
| N03 | Select several cells and run, delete, move or freeze them | Y | Y | Y | Y | P | Have | `nbselect.js` |
| N04 | Split and merge cells | Y | Y | Y | Y | N | Have | split in `notebook.js`, merge `nbselect.js:33` |
| N05 | Undo a cell delete | Y | Y | Y | Y | Y | Have | `notebook.js` |
| N06 | Find and replace across the notebook (case, word, regex) | Y | Y | Y | Y | P | Have | `nbfind.js` |
| N07 | Completion from the live kernel | Y | Y | Y | Y | Y | Have | `kernel_complete` (`sidecar/fabric_desktop/service.py`) |
| N08 | Hover docs and signature help | P | Y | Y | Y | P | Have | `nbintel.js`, `kernel_inspect` |
| N09 | Inline syntax and lint underlines | P | Y | Y | Y | N | Have | `nbintel.js` |
| N10 | Go to definition, find references, rename across cells | P | Y | N | P | N | Gap | `nbintel.js` has hover and lint only |
| N11 | Format a cell or the notebook | P | Y | Y | Y | N | Have | #470; the Format code button has its own thread |
| N12 | Multi-cursor, bracket matching, folding, comment toggle | Y | Y | Y | Y | Y | Have | CodeMirror 6 setup in `notebook.js` |
| N13 | Outline from headings; collapse a heading's section | Y | Y | Y | Y | Y | Have | `notebook.js` |
| N14 | Cell tags and titles | Y | Y | P | Y | Y | Have | `notebook.js` |
| N15 | Code snippets | P | Y | Y | N | Y | Have | `nbresources.js` |
| N16 | Drag a table into a cell; Load data menu | N | P | Y | Y | Y | Have | `nbdrag.js` |
| N17 | Editor display settings: word wrap, font size | Y | Y | P | Y | P | Partial | font fixed at 13px (`notebook.js:741`), no wrap toggle |
| N18 | Vim key bindings | Y | Y | N | N | N | Gap | |
| N19 | Rebind keyboard shortcuts | Y | Y | N | N | N | Gap | `shortcuts.js` lists keys, can't change them |
| N20 | Markdown: numbered and nested lists, tables, code blocks, quotes, H4 to H6, clickable links | Y | Y | Y | Y | Y | Partial | `markdown()` in `app.js:2900` renders H1 to H3, bullet lists, paragraphs, bold, italic and code; links aren't clickable |
| N21 | LaTeX math in Markdown | Y | Y | Y | Y | Y | Gap | |
| N22 | Images in Markdown, including pasted attachments | Y | Y | Y | Y | Y | Gap | |
| N23 | Rich-text Markdown editing (toolbar, live preview) | N | P | Y | N | Y | Gap | raw source, rendered on leaving the cell |
| N24 | Run cell, all, above, below; restart and run all; interrupt | Y | Y | Y | Y | Y | Have | `notebook.js`, `nbnext.js` |
| N25 | Run the selected text or current line | P | Y | Y | Y | N | Have | `nbselect.js` |
| N26 | Execution count and time per cell | Y | Y | Y | Y | Y | Have | `notebook.js:465` |
| N27 | A session per notebook; several notebooks running | Y | Y | Y | Y | Y | Have | Preview, `kernel_sessions` |
| N28 | Kernel status, restart, list of sessions | Y | Y | Y | Y | Y | Have | `notebook.js` |
| N29 | Variable explorer | P | Y | Y | Y | P | Have | `nbvars.js` |
| N30 | DataFrame viewer: paging, sort, search, profile | P | Y | Y | Y | Y | Have | #381, #382 (`frame_page`, `frame_profile`) |
| N31 | Debugger: breakpoints, step, inspect | Y | Y | N | Y | N | Have | Preview, `debugging.js` |
| N32 | Cell dependencies and stale outputs | N | N | N | N | Y | Have | Preview; beyond parity, not counted |
| N33 | Reactive run of dependent cells | N | N | N | N | Y | Gap | one product; not counted |
| N34 | SQL cells, result usable as a DataFrame | P | P | Y | Y | Y | Have | `%%sql`, `_sqldf` |
| N35 | Parameter cells and input widgets | P | P | Y | Y | Y | Have | `nbinputs.js` |
| N36 | Magics: `%run`, `%pip`, `%%sh`, `%timeit`, `%%capture` | Y | Y | Y | Y | P | Have | [Notebook magics](../notebook-magics.md) |
| N37 | Environment per notebook; install packages | Y | Y | Y | Y | Y | Have | Preview, `nbenv.js` |
| N38 | Progress for long operations | P | P | Y | Y | P | Have | runtime progress line |
| N39 | R and Scala kernels | Y | Y | Y | Y | P | Not counted | no JVM or R ships with the engine |
| N40 | Scratch console beside the notebook | Y | Y | N | N | N | Gap | |
| N41 | Rich tables, no-code charts, several charts per result | P | P | Y | Y | Y | Have | #391, #546 |
| N42 | matplotlib, Plotly, Altair, Bokeh | Y | Y | Y | Y | Y | Have | `chart_output.py`, `vendor/vega` |
| N43 | ipywidgets | Y | Y | Y | Y | N | Have | `ipywidgets.js` |
| N44 | No-code data cleaning | N | Y | Y | P | N | Have | `wrangler.js` |
| N45 | Collapse, scroll and clear outputs; hide code | Y | Y | Y | Y | Y | Have | `notebook.js:149` |
| N46 | Download a result as CSV, Excel or Parquet | N | P | Y | Y | Y | Have | `frame_download` |
| N47 | Find inside outputs | Y | Y | N | N | N | Gap | `nbfind.js` searches sources only |
| N48 | Publish a notebook as a read-only report or app | P | N | N | Y | Y | Gap | App Studio builds apps from a spec, not from a notebook |
| N49 | Presentation (slide) mode | P | N | N | Y | Y | Gap | |
| N50 | Autosave, versions, diff, restore | P | P | Y | Y | Y | Have | `notebook-versions.js` |
| N51 | Export as .ipynb, .py, HTML | Y | Y | Y | Y | P | Have | `notebook-formats.js` |
| N52 | Export as PDF | Y | Y | N | P | Y | Gap | |
| N53 | Git with a notebook-aware diff | P | Y | Y | Y | Y | Have | `git.js:589` Notebook view |
| N54 | Comments on cells | N | N | Y | Y | Y | Have | `notebook-comments.js` |
| N55 | Notebook files, folders and search | Y | Y | Y | Y | Y | Have | `notebook-files.js` |
| N56 | Schedules and run history | N | N | Y | Y | Y | Have | Preview, `schedules.js`, `runs.js` |
| N57 | Real-time co-editing | P | P | Y | Y | Y | Not counted | can't run on one PC; teams share through Git |
| N58 | Item sharing and permissions | N | N | Y | Y | Y | Not counted | set in Fabric after Publish |
| N59 | In-editor AI assistant | P | Y | Y | Y | Y | Not counted | AI-ready by policy: AI tools use the local API |
| N60 | Dark mode and themes | Y | Y | Y | Y | Y | Partial | skins change the look; a dark color mode is saved (`ui_color_mode`, #2569) but not applied yet: the page reads only the light tokens (`skins.js`) |

## SQL editor matrix

Products: SSMS, ADS = Azure Data Studio / VS Code MSSQL, DG = DataGrip, DBv = DBeaver, Snow = Snowsight.

| # | Capability | SSMS | ADS | DG | DBv | Snow | KratosForge | Where / what's missing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S01 | Query tabs kept between launches | Y | Y | Y | Y | Y | Have | `app/ui/query.js` |
| S02 | Highlighting for each SQL dialect | Y | Y | Y | Y | Y | Have | `sqleditor.js` |
| S03 | Schema-aware suggestions (tables, columns, aliases) | Y | Y | Y | Y | Y | Have | `sqleditor.js` |
| S04 | Format SQL | N | Y | Y | Y | Y | Have | `sql_format` |
| S05 | Find and replace, multi-cursor, folding, comment toggle | Y | Y | Y | Y | Y | Have | `sqleditor.js` |
| S06 | Live error and compatibility underlines | P | Y | Y | Y | P | Have | `sqlcompat`, ahead of the others |
| S07 | Hover a table, column or function for its details | P | Y | Y | Y | Y | Gap | no hover in `sqleditor.js` |
| S08 | Go to (peek) definition of a table, view or procedure | Y | Y | Y | Y | N | Gap | |
| S09 | Rename an alias everywhere | N | N | Y | N | N | Not counted | one product |
| S10 | Snippets and templates | Y | Y | Y | Y | P | Have | `sqltemplates.js` |
| S11 | Signature help for functions | P | Y | Y | P | P | Gap | |
| S12 | Run all or the selection; F5 and Ctrl+Enter | Y | Y | Y | Y | Y | Have | #2406 |
| S13 | Run the statement under the cursor | N | P | Y | Y | Y | Gap | `sqlStatementAt` (`sqleditor.js:183`) exists but only feeds suggestions |
| S14 | Scripts with several statements and several result sets | Y | Y | Y | Y | Y | Gap | one statement at a time ([Warehouses](../warehouse.md)) |
| S15 | Stop a running query | Y | Y | Y | Y | Y | Have | `sql_cancel` |
| S16 | Time, rows and a Messages view | Y | Y | Y | Y | Y | Have | `query.js:604` |
| S17 | Query variables and parameters | Y | P | Y | Y | Y | Gap | |
| S18 | Transactions and auto-commit control | Y | Y | Y | Y | P | Partial | warehouse changes go through Allow changes and a confirm; no open transaction across runs |
| S19 | Sort the result grid by a column | P | Y | Y | Y | Y | Gap | plain table (`drawResults` in `query.js`) |
| S20 | Filter or search the result grid | N | Y | Y | Y | Y | Gap | |
| S21 | Resize and reorder result columns | Y | Y | Y | Y | Y | Gap | |
| S22 | Open a long text or JSON value in a viewer | P | Y | Y | Y | Y | Gap | |
| S23 | Copy selected cells, with or without headers, as CSV, JSON or INSERT | Y | Y | Y | Y | Y | Partial | copies the page as TSV with headers (`copyRows` in `query.js`) |
| S24 | Export as CSV, Excel and JSON | Y | Y | Y | Y | Y | Partial | CSV and Parquet only (`sql_exec.py:975`) |
| S25 | Chart a result | N | Y | N | P | Y | Have | `sqlchart.js` |
| S26 | Column statistics on a result | N | N | P | N | Y | Gap | the notebook's profile (`frame_profile`) isn't on the SQL page |
| S27 | Result history, pin a result | N | N | Y | Y | P | Gap | |
| S28 | Results pane: resize, maximize, hide (Ctrl+R) | Y | Y | Y | Y | Y | Partial | editor resizes (`styles.css:672`); no maximize or hide |
| S29 | Object explorer: schemas, tables, views, procedures, functions, columns | Y | Y | Y | Y | Y | Have | warehouse Explorer; lakehouse lists tables and columns |
| S30 | Script as SELECT, CREATE, ALTER, DROP | Y | Y | Y | Y | P | Have | warehouse ([Warehouses](../warehouse.md)) |
| S31 | Table detail, data preview, profile | P | P | Y | Y | Y | Have | Preview, `whdetail.js` |
| S32 | Diagram of tables and their relationships | Y | N | Y | Y | N | Gap | |
| S33 | Schema compare and deploy | Y | Y | Y | Y | N | Have | `whproject.js` |
| S34 | Data compare between two tables | Y | N | Y | Y | N | Gap | |
| S35 | Edit rows in the result grid | Y | N | Y | Y | N | Gap | writes to lakehouse tables are guarded; needs a decision |
| S36 | Query history with search | P | Y | Y | Y | Y | Have | `sqlhistory.js:82` |
| S37 | Saved and shared queries | Y | Y | Y | Y | Y | Have | `sqlqueries.js` |
| S38 | Open and save .sql files on disk | Y | Y | Y | Y | P | Gap | queries are kept in the app only |
| S39 | Estimated plan, drawn | Y | Y | Y | Y | P | Partial | warehouse (`sqlplan.js`); no plan for lakehouse or local queries |
| S40 | Actual plan or profile after a run | Y | Y | Y | Y | Y | Partial | notebooks have it (`qprofile.js`); the SQL page doesn't |
| S41 | Running queries; stop someone's | Y | N | P | Y | Y | Have | Preview, `whmonitor.js` |
| S42 | Connect to other engines (SQL Server, Postgres, Snowflake, Databricks SQL) | P | P | Y | Y | N | Partial | lakehouse, SQL endpoint, warehouse and local project only |
| S43 | SQL notebooks mixing SQL and Markdown | N | Y | N | N | Y | Have | notebooks with `%%sql` |
| S44 | SSMS-style keys beyond F5 (Ctrl+R, Ctrl+Shift+U/L) | Y | Y | Y | Y | P | Partial | F5, Ctrl+Enter, Shift+Alt+F |
| S45 | Dark mode | Y | Y | Y | Y | Y | Partial | as N60 |
| S46 | Go to line, bookmarks | Y | Y | Y | Y | N | Gap | |
| S47 | In-editor AI assistant | Y | Y | Y | Y | Y | Not counted | AI-ready by policy |

## Roadmap

Each item is scored **usefulness** (1 to 5: how often a developer hits it in a normal day) times
**reach** (how many of the five products have it), divided by **effort** (S = 1, M = 2, L = 3). Higher
goes first. Items that need a mockup start when Chris has answered it, so the build order runs the
items without one first. Items that add a screen or a panel get a mockup first; items that only change what an
existing control does go straight to a draft PR.

### Wave 1: to 95%

| Order | Item | Rows | Use | Reach | Effort | Score | Mockup? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | **SQL export as Excel and JSON**, reusing the notebook's Excel writer | S24 | 4 | 5 | S | 20 | No: two items in the Export menu |
| 2 | **Run the statement under the cursor** (Ctrl+Shift+Enter, as in notebooks) | S13 | 4 | 4 | S | 16 | No: a key and a menu item |
| 3 | **Results pane**: maximize, hide (Ctrl+R), Ctrl+Shift+U/L, go to line (Ctrl+G) | S28, S44, S46 | 3 | 5 | S | 15 | No |
| 4 | **Open and save .sql files** | S38 | 3 | 5 | S | 15 | Yes (small) |
| 5 | **Markdown rendering**: CommonMark and GitHub tables, numbered and nested lists, code blocks, quotes, clickable links (opened in the browser), images from attachments, LaTeX math. Embeds markdown-it and KaTeX (both MIT) | N20, N21, N22 | 5 | 5 | M | 12.5 | No: renders what's there |
| 6 | **SQL hover and signature help** from the catalog the Explorer already read and the dialect's function list | S07, S11 | 4 | 5 | M | 10 | No: same hover as notebooks |
| 7 | **Query parameters**: `{{name}}` or `:name` values asked for before a run, kept with the tab | S17 | 4 | 5 | M | 10 | Yes |
| 8 | **Result grid**: sort, filter, resize and reorder columns, value viewer, copy selection with or without headers as TSV, CSV, JSON or INSERT, column statistics. Reuses the notebook table's engine paging (`frame_page`) and profile over the run's snapshot | S19 to S23, S26 | 5 | 5 | L | 8.3 | Yes |
| 9 | **Several statements per run, several result sets**: statements split by `;` and `GO`, a result tab for each, stop on the first error | S14 | 5 | 5 | L | 8.3 | Yes |
| 10 | **Export as PDF** (from the HTML export, through the system's print to PDF) | N52 | 2 | 4 | S | 8 | No |
| 11 | **Plan and profile for local queries**: DuckDB `EXPLAIN` as the plan view, `EXPLAIN ANALYZE` as the notebook's query profile | S39, S40 | 3 | 5 | M | 7.5 | No: existing Plan view |
| 12 | **Go to definition**: SQL F12 opens the object in the Explorer and scripts it; notebooks jump to the cell that defined a name and rename it across cells, found with Python's own `ast` over the cells | S08, N10 | 3 | 4 | M | 6 | No |
| 13 | **Transactions on a warehouse**: run `BEGIN TRAN` ... `COMMIT` as one guarded script, after item 9 | S18 | 2 | 5 | M | 5 | No |
| 14 | **Result history and pin a result** | S27 | 3 | 3 | M | 4.5 | Yes |
| 15 | **Table diagram** from warehouse keys and lakehouse naming, read only | S32 | 3 | 3 | M | 4.5 | Yes |
| 16 | **Editor settings**: word wrap and font size for notebooks and SQL; rebind keys; Vim mode (embeds `@replit/codemirror-vim`, MIT) | N17, N18, N19 | 2 | 4 | M | 4 | Yes (Settings rows) |
| 17 | **Find inside outputs** in notebook Find | N47 | 2 | 2 | S | 4 | No |
| 18 | **Notebook as a read-only report**: outputs only, from the HTML export, opened in its own window | N48 | 2 | 3 | M | 3 | Yes |
| 19 | **Scratch console** beside a notebook, in the same session | N40 | 2 | 2 | M | 2 | Yes |
| — | **Dark mode applied** to the shell, notebooks and the SQL editor (CodeMirror themes from the same tokens). `ui_color_mode` is saved but not applied (#2569) and nobody owns applying it yet. It touches the standing light-shell design rule, so it needs Chris's go and a mockup before anyone builds it | N60, S45 | 4 | 5 | M | 10 | Yes, and Chris's go |

Wave 1 closes 24 Gap rows and 11 Partial rows. Afterwards: 94.5 of 99 rows, **95%**.

### Wave 2: the last 5%

| Item | Rows | Why later |
| --- | --- | --- |
| Rich-text Markdown editing (toolbar, live preview) | N23 | Lower use; Markdown source stays the format saved |
| Presentation (slide) mode | N49 | Lower use |
| Data compare between two tables | S34 | Builds on the result grid and several result sets |
| Edit rows in the result grid | S35 | Writes to lakehouse and warehouse tables are guarded on purpose; needs Chris's decision and a Security read |
| Connect to other engines (Azure SQL Database first) | S42 | Needs a Security read (token audience, SQL auth) and Chris's go; the Azure SQL query editor check ranked it first |

## Execution plan

- **One PR per item**, each a draft until Chris reacts, at most three open at once. Branches
  `claude/editor-parity-<item>`.
- **Mockups first** for items marked Yes, gathered into one clickable mockup so Chris answers once.
  Items marked No start at once, under Chris's go for this program (2026-11-13).
- **Reuse before building**: the notebook table's paging, profile and downloads move to the SQL page
  rather than being written twice; open-source components (markdown-it, KaTeX, the CodeMirror Vim mode)
  are vendored with their licences, offline, never fetched at run time.
- **Local first**: everything above runs on this PC with no Fabric job, except the warehouse rows,
  which run in Fabric as they do today.
- **Looks**: the light KratosForge shell, one page layout, no RPG styling in editors or grids.
- **Untested is Preview**: a new feature carries the Preview tag until it has been used for real.
- **Review**: each PR goes to a Quality reviewer, UX for UI copy, Security for rendering untrusted
  Markdown, opening links, files and connections. Release management merges.
- **Keeping score**: each merged PR changes its rows here from Gap or Partial to Have, and updates
  the table under [Where we stand](#where-we-stand). A row only moves when the code is on main.
- Already owned elsewhere and counted, not duplicated: the Format code button (its own thread) and F5
  runs the query (#2406).

## Fabric notebooks: 100%

Chris asked on 2026-11-13 for 100% parity with Fabric notebooks specifically. That goes beyond this
page's editor rows into what this PC's Spark runs. The Notebook parity thread owns it, in this order,
each its own PR and Preview until used for real:

| # | Item | Mockup |
| --- | --- | --- |
| F1 | Python UDFs: `F.udf`, `@udf`, `spark.udf.register` (usable from `%%sql`), `pandas_udf` | No |
| F2 | `mapInPandas`, `mapInArrow`, `groupBy().applyInPandas`, `foreach`, `foreachPartition` | No |
| F3 | DataFrame leftovers: `freqItems`, `inputFiles`, `to(schema)`, `withMetadata`, `writeTo(...)` | No |
| F4 | `spark.catalog.listTables`, `listDatabases`, `listFunctions` from the attached lakehouse | No |
| F5 | MERGE with schema evolution and dynamic partition overwrite, under the write lease | No |
| F6 | `OPTIMIZE`, `VACUUM`, `RESTORE` and `DeltaTable` maintenance from code, behind the app's guards | No |
| F7 | `notebookutils` writes: `lakehouse.loadTable`, `create`, `update`; `notebook.create`, `update`, `updateDefinition` | No |
| F8 | Rich-text Markdown editing (N23), after the Markdown renderer | Yes |
| F9 | Editor display settings (N17, Wave 1 item 16) | Yes |
| F10 | Dark mode applied (N60), only with Chris's go | Yes |

What can't run on a PC stays a one-click hand-off to Fabric rather than a silent failure: real Spark
executors and Scala, R and .NET cells, `rdd` and pandas-on-Spark, continuous streaming and watermarks,
Python UDTFs, `fs.mount` of other storage, managed private endpoints and workspace identity, the Spark
UI (query progress and profile run locally instead), co-editing and sharing, and Copilot (out by
policy). 100% means every Fabric row is done on main or is one of these hand-offs with a working path.

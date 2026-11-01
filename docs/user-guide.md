# KratosForge user guide

KratosForge runs your data work on your own PC and keeps the results in Microsoft Fabric's OneLake. It looks and works like Fabric, so most pages will feel familiar. This guide walks through every page: what it is for, how to use it, and what happens behind it.

The app's **Learn more** links open the sections of this page. Two areas have their own, longer guides:

- **[Notebooks and SQL](notebooks.md):** writing and running notebooks on your PC, Fabric code that works here, publishing, Run in Fabric and schedules, and the SQL query tab.
- **[Side by side](side-by-side.md):** running a notebook on this PC and in Fabric one after the other, comparing time and results, and the TPC-H benchmark.
- **[Notebook resources](notebook-resources.md):** files kept beside a notebook and read in its cells as `builtin/<file>`, like Fabric's built-in resources.
- **[Validate a notebook](validate-notebook.md):** checking a notebook works from the top with a clean run, and cleaning up its code.
- **[Notebook files](notebook-files.md):** finding, opening, copying, deleting and restoring notebooks, and editing a Fabric notebook on this PC.
- **[Notebook folders](notebook-folders.md):** grouping notebooks into folders, moving a notebook into one, renaming and deleting folders.
- **[Fabric and Databricks notebook formats](notebook-import-export.md):** bringing in Fabric and Databricks source and .dbc archives (and .ipynb), and saving a notebook in those formats or as a web page.
- **[Notebook version history](notebook-versions.md):** earlier versions of a notebook, checkpoints, Compare and Restore.
- **[Profile](profile.md):** a private record of work KratosForge verified, and a review-ready report.
- **[Notebook comments](notebook-comments.md):** comment threads on cells and lines of code, kept in the notebook file.
- **[How a proof works](prove/how-proofs-work.md):** Prove this notebook: both runs from the same pinned inputs with sandboxed writes, the differences, the scorecard and the receipt.
- **[Metadata framework](metadata-guide.md):** building and running medallion layers from config, step by step, with a reference for every setting.

New here? Install first ([install.md](install.md)). Setting it up for others? Start with [Configure Azure and Fabric](azure-setup.md).

**Where things run.** Unless a button says Fabric, work runs on your PC and starts no Fabric job (its OneLake reads and writes still use capacity): notebooks, SQL queries, metadata runs in an environment set to **This PC**, profiling, comparing and testing. Buttons that say **Fabric** (Run in Fabric, Fabric schedules, Optimize in Test or Prod, semantic models, environments set to **Runs on: Fabric**) use your workspace's capacity.

## The window

| Part | Where | What it does |
| --- | --- | --- |
| **Left rail** | Left edge | The pages: Home, Create, Workspaces, OneLake, Data mesh, Platform (Preview), Fabric, Monitor, and Settings at the bottom. Below them are the items you opened recently (a workspace, a SQL query, a notebook), each with an **×** to close it. Closing a notebook saves it first. |
| **Back and Forward** | Top bar, left | Go back to the page you were on before, tab included, and forward again (**Alt+Left** and **Alt+Right**). Back after **Open in Fabric** returns to the page you opened it from. When you start the app again, it opens on the page you were on last, or on Home if that page is hidden or gone. |
| **Search box** | Top bar | Opens the command bar (below). When you pick **Show only items matching ...**, the current page is filtered by that text until you clear the box. |
| **Environment picker** | Top bar, on Data mesh pages only | Dev, Test or Prod. Every Data mesh page shows the environment picked here. The pill next to it says where that environment runs, for example "Runs in Fabric · Sales - Test"; click it to change that. |
| **Bell** | Top right | The activity tray (below). The number on it counts what is running plus new problems; it turns red when there's a problem you haven't seen. |
| **Account circle** | Top right | Your initial, or **?** when signed out. Opens the account menu. |
| **Browse…** | Beside every box that asks for a folder on this PC | Opens the Windows folder dialog and puts the folder you pick in the box. **Cancel** leaves the box as it was, and you can still type or paste a path. A folder your organization sets has no **Browse…**. |
| **Info button** | Title bar, beside the page's title | Opens the page's one-line explanation and its **How this works** panel (what the page reads, what it changes, where results go) as a drawer. With **Settings > Appearance > Layout > Explanations** set to **Beside the page**, they show on the page instead ([Layout](#layout)). |

### Command bar (Ctrl+K)

Press **Ctrl+K** anywhere, or click the search box. Type a few letters to find:

- **Pages**, such as Monitor or Settings.
- **Actions**, such as New notebook, New lakehouse, Run all entities, Data mesh: Surrogate keys, OneLake shortcuts, Report a problem, What's new or Sign out.
- **Notebooks** on this PC, **workspaces** you can open, and **entities** in your metadata project.

Use the arrow keys and **Enter**, or click. **Esc** closes it. Actions for preview features only show when preview features are on.

### Using the keyboard

- **Tab** moves through the window, with a ring around whatever has focus. The first Tab offers **Skip to content**, which jumps past the top bar and the rail.
- In a dialog, **Tab** stays inside it, and **Esc** closes it when clicking outside would. Focus then goes back to what you were on.
- A menu (your account, Publish options, Git) opens on its first item. The **Up** and **Down** arrows move through it, **Enter** picks, and **Esc** closes it and goes back to its button.
- Rows and cards that open something when clicked, such as a notebook, lakehouse, run or workload, can be reached with **Tab** and opened with **Enter** or **Space**.
- In a row of tabs, **Left** and **Right** move to the next tab, and **Home** and **End** to the first and last.
- **Alt+Left** goes back to the page before, and **Alt+Right** forward again, like the arrows at the top left. They don't while you're typing in a box or an editor, or while a dialog is open. **Go back** and **Go forward** are in the command bar too.
- While you're working inside the embedded Fabric page (**Workspaces > Fabric portal**), **Alt+Left** and **Alt+Right** may move within that page instead. Use the **Back** and **Forward** buttons at the top left to leave it.
- If Windows is set to show fewer animations (**Settings > Accessibility > Visual effects > Animation effects** off), the app doesn't animate either, unless you untick **Respect Windows' reduced-motion setting** in [Motion and effects](#motion-and-effects).
- With a Windows contrast theme on (**Settings > Accessibility > Contrast themes**), the app uses the theme's colors. The current page in the rail, the selected tab or option, and a selected notebook cell are drawn in the theme's highlight color, and the focus ring uses it too.

### Keyboard shortcuts

Press **?** when you aren't typing in a box to see every shortcut. The list also opens from the command bar (**Keyboard shortcuts**) and from **Settings > Help and about > Keyboard shortcuts**. It is drawn from the same table the app reads its keys from, so it always matches what the keys do.

| Keys | What they do |
| --- | --- |
| **Ctrl+K** | Open the command bar |
| **?** | Show the list of shortcuts |
| **Esc** | Close the command bar, a menu, a dialog or the activity tray |
| **Tab**, **Shift+Tab** | Move to the next or previous control; in a dialog, focus stays inside it |
| **Enter** or **Space** | Open the row or card that has focus |
| **Alt+Left**, **Alt+Right** | Go back or forward to the page you were on, as the top bar's arrows do (not while typing in a box) |
| **Up**, **Down**, **Home**, **End** | Move through an open menu |
| **Left**, **Right**, **Home**, **End** | Move between tabs |

Some pages add their own keys to the list while you're on them: **Ctrl+S** saves the notebook, and on an entity page saves the entity file (where **Tab** types four spaces; press **Esc**, then **Tab**, to move on); **Ctrl+Enter** in the commit message commits on Source control. While a notebook is open the list also shows its own keys (run a cell, command mode, working on several selected cells, find and replace); the [notebook guide](notebooks.md#editing) explains them.

### The bell: activity

The bell keeps a list of what happened in this app on this PC:

- **Runs that are still going**, including ones in Fabric. It follows a Fabric run until it ends, even if you go to another page, and tells you how it ended.
  A running Fabric job this app started has **Cancel run** ([Preview](#cancel-a-fabric-run-preview)).
- **Results you can reopen**, such as the result of a Run all.
- **Every error**, explained in a sentence ("Your sign-in expired", "You don't have permission for that in this workspace"), with the technical text folded under **Details**. When you report a problem, the most recent errors are included for you.

### Account menu

Click your initial at the top right:

| Item | What it does |
| --- | --- |
| Signed in as ... | Who you are signed in as |
| **Settings** | Opens [Settings](#settings) |
| **Help and user guide** | Opens this guide |
| **Report a problem** | See [Report a problem](#report-a-problem) |
| **What's new** | The release notes of the build you're running, and everything new in the 30 days before it |
| Build and channel | Which build you're on, and Stable or Preview |
| **Sign out** | Forgets your sign-in on this PC, in the app and in the embedded Fabric pages. Notebooks and settings stay. |

## Get started

1. **Sign in** with your work account, the same one you use for Fabric. Windows often signs you in with no prompt. There's no app ID to ask anyone for.
2. **Open a lakehouse** from **OneLake** or **Workspaces** to browse its tables and files, or query them with SQL.
3. **Create a notebook** from **Create**. Cells run on this PC, not on your Fabric capacity.
4. **Set up a metadata project** (Data mesh in the left rail) when you want KratosForge to build and run your medallion layers for you.

Home shows **Getting started with KratosForge** under **Start something**: one small tile for each main starting point (run a notebook on this PC, bring your notebooks from Fabric or Databricks, explore OneLake, build medallion tables, spend less Fabric capacity, set up the platform), each with a button that opens it. A tile gets a tick once you've tried it. Click the heading to fold the panel to one line; Home remembers that, and the heading opens it again.

## Home

Home answers "what happened since I was last here".

Home shows as soon as the app opens. While KratosForge checks its components and looks for a newer build, Home reads "Starting the local engine..." with placeholders, and the **Start something** buttons wait. When the check is done, each section fills in. If the check takes more than a moment, or something needs updating or fixing, the **Checking components** window opens on top.

From the top:

| Section | What it shows |
| --- | --- |
| **Summary line** | "Nothing needs your attention", or how many things do, and how many are running. When run history, an environment or Fabric jobs couldn't be read, it says "Nothing found that needs your attention" and names what it couldn't check, since unknown isn't the same as fine. Signed out, it says to sign in so KratosForge can check Fabric. |
| **Start something** | **Continue** (the notebook you changed last), New notebook, Import from Fabric or Databricks, New lakehouse, OneLake catalog. |
| **Getting started with KratosForge** | Six starting points, each with a button that opens it, and a tick once tried. Click the heading to fold it to one line; Home remembers that. See [Get started](#get-started). |
| **Pinned** (Preview) | Notebooks and Fabric items you pinned, with **Open**, and **Run** on a notebook. Shows once something is pinned. See [Pinned items](home-notebooks.md#pinned-items-preview). |
| **Recent notebooks** | Your last eight notebooks, with each one's workspace, lakehouse, where it runs and how it last ran. Click to open. Tick several and press **Run** to run them at once (Preview). The section folds with its arrow. See [home-notebooks.md](home-notebooks.md). |
| **Ready to work** | Whether this PC can do real work yet, with a fix for anything that's missing. While it shows, it's the one place Home asks you to sign in. See [Ready to work](#ready-to-work). |
| **Needs your attention** | Tables whose latest run failed or is waiting for a decision, in any environment, with **Open entity** and **Open run**. Fabric runs that failed in the last day, with **Open in Fabric**. Tables that are late for their [freshness promise](metadata-guide.md#freshness-promises). |
| **Your workloads** (Preview) | What you own, by health, with the ones that need attention and how many workloads have no owner. See [Workloads](#workloads-preview). |
| **Running now** | What this app is running, here or in Fabric. |
| **Coming up** | The next scheduled runs, on this PC and in Fabric. **All schedules** opens Monitor > KratosForge > Schedules. |
| **Data mesh** (Preview) | A map of the whole mesh: each domain and subdomain, the projects in it, the data products they publish (colored by who may read them) and arrows from each product to the projects that read it. Your open project is outlined; click it to open Data mesh > Domains > This project. The section folds with its arrow. |
| **Environments** | Dev, Test and Prod: where each runs, and "All green", "2 failed", "Running" or "Never run". Click one to see its runs. Hidden until you have a metadata project. |
| **Estate X-ray** (Preview) | See below. |

### All notebooks

**All notebooks**, under **Recent notebooks**, lists every notebook on this PC and in your notebooks repositories, with search inside them. The [notebook files guide](notebook-files.md#all-notebooks) has the details.

### Notebook folders

On **All notebooks**, **New folder** makes a folder, and a notebook's **⋯** menu has **Move to folder…**. Notebooks are grouped by folder, with counts, and each folder's menu can rename it or delete it when it's empty. A folder is part of the notebook's name (Sales/Daily load), so its resources, versions and this PC's schedules follow it. Publishing names the Fabric item without the folder (Preview). See [Notebook folders](notebook-folders.md).

### Ready to work

A short list that says whether this PC can do real work yet. Each check only reads, and none opens a sign-in window. The **How this works** panel under the list (folded to its title until you open it) says what it reads, the one thing it can write and where results are kept.

| Check | Ready when | If not |
| --- | --- | --- |
| **Local engine** | The engine starts on this PC | You wouldn't see Home; the startup window explains |
| **Signed in** | Your sign-in is saved and still works | **Sign in** |
| **Fabric answers** | Fabric lists your workspaces | The error, and **Check again**. No workspaces: ask to be added to one |
| **A workspace on a capacity** | At least one of your workspaces is on a running Fabric capacity | You can't create lakehouses yet; notebooks and SQL on this PC still work. [Plan workspaces and capacity](azure-setup.md#5-plan-workspaces-and-capacity) |
| **Your organization's settings** | Nothing in your organization's policy file was ignored | **Open Settings** shows what was ignored |
| **Writing to OneLake** | **Test a write** worked in the last 7 days | Only checked when you ask (below) |

**Test a write** is the one check that writes. You pick a workspace and lakehouse. Workspaces whose name says Prod aren't offered, and the engine refuses them too. It writes `Files/_fabric_desktop_check/write-test.txt`, reads it back and deletes the folder, even if the read fails. No table is touched.

A passed write test counts for 7 days, for the same account, tenant and lakehouse; after that Home asks again. **Test again** reruns it at any time. A failed or refused test, and signing out, forget the pass.

When every check passes, the list folds into one line. **Hide** removes it on this PC.

### Estate X-ray (Preview)

The X-ray reads every lakehouse you can reach and lists what needs attention, worst first: too many small files, tables Fabric shows as Unidentified, wide access, cost, tables that stopped changing, tables that need a vacuum and empty lakehouses. It changes no lakehouse it reads; nothing changes there until you press a fix on a line.

- The first scan on a PC starts by itself after you sign in. After that, press **Scan again** when you want fresh results. **Pause** holds a scan where it is.
- The card folds to one line with the totals, and stays as you left it. The list shows six findings; **Show all** opens the rest and **Show fewer** closes them again.
- **Optimize** on a table first asks: it names the table, says it runs in Fabric and uses capacity, and shows the [risk check](finops/risk-check.md) for that workspace's capacity. A blocked check has no **Optimize** button.
- **Vacuum** on a table first says how many old files it would delete and how much storage they take, then deletes them in Fabric. It always keeps the history the table's layer promises, so Restore keeps working.
- **Your lakehouses** lists every lakehouse it read. **Adopt** on a lakehouse turns its tables into metadata entities, so the framework manages them from then on.
- When this PC is connected to a central lakehouse you can write to (**Settings > Optimize lakehouses**), each scan also adds its lakehouse and table findings there, with a recommendation for each one Optimize or Vacuum fixes, so **Optimize > Opportunities** shows them. Anyone who can read the central lakehouse can read those rows, including the workspace, lakehouse and table names of every workspace your scan reached, even ones they can't open in Fabric. The app hides items a person can't open, but that is a courtesy, not security. Guests' and apps' names and cost estimates stay on this PC. A line under the totals says what was added, or why it couldn't be.
- Every finding and its fix is explained in [xray-fixes.md](xray-fixes.md).
- **How this works**, folded under the findings until you open it, says what the scan reads and which three buttons write: **Optimize**, **Vacuum** and **Adopt**, each of which asks first.

## Create

New items:

- **Notebook:** a new Python notebook, saved on this PC. See [Notebooks](notebooks.md).
- **Notebook from a template** (Preview): a notebook written from a fixed template (clean a table, join and aggregate, load a file) with key checks and the columns a proof matches rows by. See [Notebooks](notebooks.md#your-first-notebook).
- **Lakehouse:** asks for a workspace and a name, then creates a schema-enabled lakehouse in Fabric (tables live under schemas, `dbo` by default) and opens it. Needs a workspace on a capacity, and Contributor or higher on it.
- **Pipeline** (Preview): opens a Fabric pipeline read-only and runs it on this PC. See [Pipelines](#pipelines-preview).

## Pipelines (Preview)

**Create > Pipeline** opens a Fabric pipeline: one of the `.DataPipeline` folders in the repositories this PC opened (Source control), a `.DataPipeline` folder you choose, or a pipeline JSON file (`pipeline-content.json`, or a definition with parts). It opens read-only: you edit pipelines in Fabric or in your repository.

**In a Fabric workspace** (Preview) lists the pipelines of a workspace you pick, and opening one reads its definition from Fabric. That's a read: no Fabric job starts and no capacity is used. The definition stays in memory while the page is open; nothing is saved on this PC except which workspace you picked. A pipeline opened this way has **Open in Fabric** beside Check again.

The page shows the pipeline's **Graph** (each column runs after the one before it; click an activity for how it runs here), **Compatibility** (every activity, nested ones included, with whether it runs here, needs a mock or runs only in Fabric, and why), **Runs** (this pipeline's runs on this PC) and **Definition** (`pipeline-content.json`, with Copy). **Check again** reads the definition again.

**Run on this PC** asks for what the run needs, and checks it as you go:

- **Parameters**, filled with the pipeline's defaults or the values of the last run here. SecureString values are never kept on this PC; type them again each time.
- **Activities that need a mock**: for each one that can't run here, whether it ends Succeeded or Failed and the output later activities read.
- **Data steps** (Lookup, Get Metadata, Script): whether they may read lakehouses (off by default; off, they need a mock), the pipeline's workspace when a lakehouse connection doesn't name it, and for each Script whether it runs on the lakehouse (read-only) or in one of your local data projects. A Script that changes tables, or needs the SQL page's confirmation, is refused; give it a mock.
- **Notebook steps**: the notebook saved in KratosForge each Notebook activity runs, whether notebook steps may reach the network and files on this PC (asked again each run), and whether to keep what they wrote.

**Run** stays off until every activity can run here or has a mock. The run view shows every activity on a time line, nested ones under their loop, branch or child pipeline, and the output and error of the one you click. **Stop** ends the run: nothing new starts and running steps stop. **Run again with the same settings** opens the Run dialog with the settings that run started with. No Fabric job starts; notebook steps write only to a temporary folder in the lakehouse, and OneLake reads and writes use a little capacity. Results can differ from Fabric: see [Local pipeline runs](orchestration/local-runs.md).

### Data Factory copy jobs (Preview)

The **Copy jobs** tab beside **Pipelines** lists the copy jobs in a workspace you pick. Opening one shows its source and destination, its mode (Batch, or change data capture), its timeout and how many connections it uses. Two views follow:

- **Tables**: each table it copies, with the source and destination table, the write behavior (Append, Overwrite, Upsert with its keys) and whether it copies incrementally, and on which columns.
- **Run history**: the copy job's runs in Fabric, newest first.

A line under the summary says whether a run on this PC will be able to take the job. That needs both ends to be lakehouses and a Batch job, because other sources need Fabric's connections and gateways. Reading starts no Fabric job. **Run in Fabric…** starts the copy job in Fabric. It uses capacity and copies each table into the real destination, so it first shows what will happen and the risk check, and starts only when you confirm. If the workspace isn't one the app works in yet, it offers to pin it and read it again first. Running one on this PC comes later. **Open in Fabric** opens the job's workspace inside the app.

### Data Factory dataflows (Preview)

The **Dataflows** tab beside **Pipelines** lists the Dataflow Gen2 items in a workspace you pick. Opening one shows:

- **Queries (M)**: each query's Power Query code and where it loads. Destination and helper queries are folded under **Hidden queries**.
- **Destinations**: each query that loads somewhere, with the item kind, table and update method (Replace or Append), plus the connections the dataflow names.
- **Refresh history**: the dataflow's refreshes in Fabric, newest first.

Reading starts no Fabric job. Dataflows run in Fabric only, because nothing outside Microsoft's services can run Power Query. **Refresh in Fabric…** starts a refresh. It uses capacity and writes to every destination the dataflow has, so it first shows what will happen and the risk check, and starts only when you confirm. To change a query, **Open in Fabric** opens Fabric inside the app.

### Data Factory connections (Preview)

The **Connections** tab beside **Pipelines** lists the Fabric connections you can use: name, type and gateway, never credentials. For each one, **Used by** names the pipelines, copy jobs and dataflows that use it, with one column per environment (Dev, Test, Prod) showing where it's used. Without environments, the tab checks the workspaces Monitor > Fabric > Schedules lists. When an item uses a connection you can't use (deleted, or not shared with you), it shows at the top as **Missing**, because that step fails when you run it. Someone who can use the connection, such as its owner's schedule, may still run it. The tab only reads: no Fabric job starts and no capacity is used. The first time, the app asks for permission to read your connections and gateways. Gateway names show under the gateway type, and if you decline the gateway permission, the tab still lists the connections without them and doesn't ask again until you restart KratosForge. If reading the gateways fails for another reason, such as the network, **Refresh** tries again. **Manage connections in Fabric** opens Fabric's own page to add or fix a connection. KratosForge never creates or changes one. **Refresh** reads everything again.

## Notebook resources

In a notebook, the explorer's **Resources** tab lists the notebook's **Built-in files** above the code snippets. **Add files** copies files from this PC into a folder beside the notebook (`<name>.resources/builtin/`), never over a file of the same name; each file's menu has Copy path, Show in folder, Rename and Delete. Cells read the files as `builtin/<file>`, as in Fabric, and `notebookutils.nbResPath` is the folder. Up to 100 files of up to 50 MB each. The files move with the notebook into a notebooks repository and Git; publishing to Fabric doesn't upload them yet. See [Notebook resources](notebook-resources.md).

## Fabric and Databricks notebook formats (Preview)

**Import from Fabric or Databricks** under **Start something** on Home reads Fabric or Databricks source (`.py`, `.sql`, `.scala`), a Databricks archive (`.dbc`), a percent-format script (`# %%`) or a `.ipynb`, shows each notebook it would create, and creates them as new notebooks in the app's notebooks folder. A notebook that already has the name is never replaced; the new one gets the next free name, such as `Sales (2)`, and the picked file is only read. In a notebook, **Export** (Home tab > Share) saves it as `.ipynb` (with or without the outputs on screen), Fabric source, Databricks source or a web page, as a new file in your Downloads folder; a file already there is never replaced. Nothing is sent to Fabric. See [Fabric and Databricks notebook formats](notebook-import-export.md).

## Validate a notebook (Preview)

In a notebook, **Validate** (Home tab > Check, or the Run tab) opens a panel with two tabs. **Problems** lists what the code checks find, each with its cell and fix, and **Run clean check** runs every cell from the top in a fresh kernel on this PC, with table writes kept in a sandbox folder that is deleted afterwards. **Clean up** lists tidy-ups you tick and apply (unused or repeated imports, spacing, empty cells, print lines), which Undo puts back until you edit again, and suggestions such as a table read twice. See [Validate a notebook](validate-notebook.md).

## Notebook runs (Preview)

In a notebook, **Recent runs** (Home tab > Schedule, or the Run tab) lists the notebook's recent runs beside the cells: its scheduled runs on this PC and the runs this app started for it in Fabric, each with when, where, what started it, status and time taken. A run on this PC has **Details**, a Fabric run **Open in Fabric**. It reads the run logs Monitor > Runs reads; only **Refresh status**, for a Fabric run still going, asks Fabric, and it saves the answer in the app's run log on this PC. See [Recent runs](notebooks.md#recent-runs-preview).

## Notebook files

**All notebooks** (on Home, under Recent notebooks) lists every notebook, with a search that finds names and text inside cells. From there you can **Open a notebook file** from anywhere on this PC (a copy is added; the file isn't changed), **Save a copy**, **Rename**, **Move** and **Delete**, which keeps the notebook in **Recently deleted** for 30 days and turns its schedules on this PC off until you restore it. In Workspaces, **Edit on this PC** (Preview) copies a Fabric notebook here, linked to its item; Publish then shows any changes made in Fabric before it would replace them. See [Notebook files](notebook-files.md).

## Notebook version history

In a notebook, **Versions** (Home tab > Share) lists the notebook's earlier versions on this PC: one per save (one every 5 minutes while you edit, kept 30 days) and named checkpoints you create. **Compare** shows the cells that changed, and **Restore** puts a version's cells back after keeping the notebook as it is now as a version. See [Notebook version history](notebook-versions.md).

## Notebook comments

In a notebook, select lines of a cell's code (or none, for the whole cell) and choose **Add comment** in the cell's **...** menu. **Comments** on the View tab opens a panel with every thread, filtered by **Open** or **Resolved**, with **Go to cell**, **Reply**, **Resolve** and **Reopen**, and **Edit** and **Delete** on your own comments. A badge on a cell counts its open threads. Comments are written into the notebook file on this PC, so they go through Git with it; they aren't published to Fabric, and **@name** only highlights a name (nobody is notified). Each comment keeps its author's account email in the notebook file, so it goes into Git when the notebook is committed. See [Notebook comments](notebook-comments.md).

## Notebook best practices (Preview)

**Best practices** on a notebook's View tab opens a panel that checks the notebook's code cells against the notebook rules (NB001 to NB012, NB018 to NB020) as you type: secrets in code, collect() without a limit, CSV read without a schema, unpinned %pip, Python UDFs, short VACUUM retention and the rest. Each finding has its severity, what to do, and **Go to cell** (to the line). Nothing runs and no capacity is used. The governance policies in force for the open project tune the rules: one a policy turns off isn't shown, and a policy can change a rule's severity. See [Notebook best practices](notebook-best-practices.md).

## Workspaces

Every Fabric workspace you can open. Workspaces has two tabs: **Your workspaces**, below, and **Fabric portal**, which is [Fabric's own portal](#fabric) inside the app.

- **+** next to a workspace lists its items. Each item opens in the [Fabric page](#fabric); lakehouses also have **Open** and **SQL query**. The pin on an item's row pins it to Home (Preview, see [Pinned items](home-notebooks.md#pinned-items-preview)).
- The **pin** keeps a workspace at the top, in the order you pinned them. The **star** marks a favorite. The chips at the top show **All**, **Favorites** or **Pinned**. Pins and favorites are kept on this PC.
- Click a workspace's row to open it: its lakehouses, with **New lakehouse**, **New notebook** and **Open in notebook**.
- **Open in Fabric** opens the workspace in the Fabric page.

### Item preview (Preview)

Click an item's row (not its name) to see it in a panel on the right, without leaving the page. **×** closes the panel.

| You click | The panel shows |
| --- | --- |
| A table | Its first 100 rows, its column types (the **Schema** tab) and its row count, read on this PC from OneLake |
| A data file (CSV, TSV, Parquet, JSON) | Its first 100 rows. Files over 200 MB aren't downloaded for a preview, and a previewed file is deleted from this PC as soon as its rows are read |
| A text file (txt, md, py, sql, yaml and similar) | The start of the file |
| A notebook | Its cells, read only, from its Fabric definition. Outputs aren't shown, since Fabric keeps them with each run |
| A lakehouse | Its tables and files. Click one to preview it; **Back** returns to the lakehouse |
| A report, semantic model, pipeline or any other item | Fabric's own page for it, inside the panel, signed in as you. **Open full page** opens it in the Fabric page |

Previews only read; nothing is changed. They use the permissions sign-in already has.

### A lakehouse

Opening a lakehouse shows its **Tables** (by schema) and **Files** (folders and file sizes) as a tree, with **Open in new notebook** and **New SQL query**. Click a table or file to [preview](#item-preview-preview) it beside the tree.

### SQL queries

**New SQL query** on a lakehouse, or **SQL query** on a lakehouse row in OneLake or Workspaces, opens a query tab for it. Queries run on this PC with DuckDB, so no Fabric job starts, though reading OneLake still uses some capacity. Dialects, the compatibility check, the editor and formatting are in the [notebook and SQL guide](notebooks.md#sql-query). Above the result, **Messages** (Preview) says what the last run did and **Chart** (Preview) draws a quick chart of the rows already on this PC, without running the query again ([more](warehouse.md#results-messages-and-chart)).

### Warehouse queries (Preview)

**SQL query** on a **Warehouse** or **SQL analytics endpoint** row in Workspaces opens the Warehouse page: the same editor and results, with T-SQL that runs in Fabric on the warehouse. It uses capacity, so the first run on each warehouse after the app starts asks first. It only reads until you tick **Allow changes**; then a script that changes the warehouse shows what it changes and runs after you confirm, in one transaction, and in Prod you type the warehouse's name first. It signs in as you, so the warehouse's own permissions apply. The explorer lists schemas, tables, views, procedures and functions from the warehouse's catalog, with column types, and completes names as you type. **SQL from a template** (the arrow beside **New SQL query**) opens T-SQL templates (tables, loads, views, procedures, time travel, security, statistics and accelerators such as an SCD type 2 merge) with fields to fill in; the script opens in a new tab and runs nothing. **New template** adds your own, kept on this PC or in a Git repository's `sql-templates` folder for your team, and **Import** and **Export** share them as `.sql` files. **Save query** keeps a tab as a `.sql` file in My queries or in a Git repository's `sql-queries` folder, and the explorer's **Queries** tab opens them again. The **History** tab lists your runs on this PC and opens Fabric's query insights (long running, frequently run, recent requests). The **Project** tab keeps the warehouse as `.sql` files in a Git repository, in the layout Fabric's Git integration uses: **Import** scripts the warehouse into it, and you edit, save and commit the files. **Estimated plan** shows how Fabric would run a query as a tree of operators with estimated rows and cost, without running it. **Compare** shows which objects differ between the project and the warehouse, side by side, and **Deploy** previews and runs the changes that make the warehouse match, with Test and Prod deploying only what's committed and pushed. More in [Warehouses](warehouse.md).

### Warehouse monitoring (Preview)

**Monitoring** on the Warehouse page shows Fabric's workspace monitoring for the warehouse: queries, typical and slowest times, failures and who runs what, from the workspace's monitoring KQL database. It needs a workspace admin to turn on monitoring in Fabric first. **Build views** creates a KQL queryset with the same views in the workspace, after you confirm, and can do it for new warehouses there without asking again. See [Warehouse: Monitoring](warehouse.md#monitoring-preview).

## OneLake

The OneLake page has four tabs: **Catalog**, and the preview tabs **Data products**, **Shortcuts** and **Access**. **Data products** opens **Data mesh > Products**, where data products now live.

### Catalog

Every item in every workspace you can open: lakehouses, warehouses, notebooks, semantic models, reports, pipelines and the rest.

- **Search** matches names, workspaces, types, descriptions and the table names inside lakehouses. Table names are read in the background, so search by table works a few seconds after the page opens.
- **The left side** narrows the list: All items, My workspace, Endorsed items, Favorites, or one workspace (**More workspaces...** shows the rest, and **Find a workspace** filters them).
- **Type** narrows by item type, grouped as Fabric groups them (Data, Insights, Processes, Solutions, Configurations). Pick a group or open it to pick one type. **Clear** resets it.
- **Click a column heading** to sort by it; click again to reverse.
- **Star** an item to make it a favorite. Fabric has no public API for its own favorites, so these are kept on this PC.
- **Endorsement** (Certified or Promoted) shows in the Fabric catalog embedded on the page. KratosForge's own list greys out Endorsed items, because reading endorsement needs an admin-only permission it doesn't ask for.
- **Layer** shows which metadata layer a lakehouse is, when it belongs to your metadata project.
- **Environments** on the left (**All**, **Dev**, **Test**, **Prod**) shows only that environment's items, and **Location** marks each item's environment. See [Environments and solutions](#environments-and-solutions).
- **Group by** puts the list under headings: **Environment**, **Solution**, or **Solution, then environment**. Click a heading to open or close it; the first one starts open. Your choice is kept on this PC.
- On a lakehouse row, **▸** shows its tables and files, **SQL query** opens a query tab, and **Open in notebook** starts a notebook with it attached. **Show in Fabric** (any item) opens it in the Fabric page.
- **Open Fabric's catalog** opens Fabric's own OneLake catalog.

Workspaces you can't read are skipped and named at the top.

#### Environments and solutions

A workspace's **environment** comes from your metadata project first: the workspace each environment writes to and its Keys workspace (**Data mesh > Environments and promote**). Otherwise it comes from a word in the workspace name: Dev or Development; Test, TST, UAT or QA (all shown as Test); Prod, PRD or Production. The word must stand apart, as in `Sales Dev`, `Sales-PROD`, `Sales [Test]` or `DEV_Sales`. When a name has more than one, the last wins, so `Production Planning Dev` is Dev. A workspace with neither shows under **No environment**.

A workspace's **solution** is its name without that environment word, so `Adv Wrks DE 2 Dev`, `Adv Wrks DE 2 Test` and `Adv Wrks DE 2 Prod` all belong to the solution **Adv Wrks DE 2**. To make your workspaces group together, name each environment's copy the same way with a different environment word.

### Shortcuts (Preview)

Shortcuts are tables or folders in a lakehouse that point at data somewhere else, without copying it. This tab is the **shortcut command center**: every shortcut in every lakehouse you can see, in one list.

- **All shortcuts:** where each one lives, what it points at, whether it works, which managed tables read it, and its last change. Select one to see who can read it and its history, and to **Check now**, **Repoint** or **Remove** it.
- **Health:** broken shortcuts by cause, and ones crossing environments or duplicated, each with a fix.
- **Access:** shortcuts whose readers and target don't match, and the platform's shortcut rules (platform admins set them).
- **Changes:** what was added, repointed, removed, broke or got fixed, and by whom, with **Undo** and **Recreate**.
- **Requests:** shortcut requests, and shortcuts that need changing.
- **New shortcut** puts a table under a schema or a folder under Files, pointing at another lakehouse (OneLake), or at ADLS Gen2 or Amazon S3 through a Fabric cloud connection. **Remove** deletes the shortcut, not the data it points at.

The full guide is [Shortcut command center](shortcuts.md). Needs `OneLake.ReadWrite.All`, and `Connection.Read.All` for ADLS and S3 ([permissions](permissions.md)).

### Access (Preview)

Who can reach your workspaces, and who can read which tables. The tab has three views.

**All workspaces** shows every workspace you can open, from the access cache in the central lakehouse ([how the cache works](optimize/README.md#the-access-cache-fdacc_)). It needs a connected central lakehouse (Settings > Optimize lakehouses).

- Each row counts the workspace's people, groups, apps and guests, and says how many things are **worth a look**. A workspace whose roles couldn't be read at the last refresh keeps its last known list, marked **Out of date**.
- **Refresh changes** reads every workspace again as you, and writes only what changed. The first time, it asks before adding the cache's tables to the central lakehouse. Nothing in the workspaces it reads changes.
- Filter by **Worth a look**, **Guests**, **Write roles by name** or **Out of date**, or find a workspace or person by name.
- Click a workspace to see its people and its lakehouses' table roles, with **guided fixes**: people with write roles by name, guests, apps with Admin, and lakehouses without data access roles. **Review in Workspace roles** opens it in Environments, and **Table access** opens the lakehouse. Both ask before they change anything in Fabric.
- **Who may read it** limits the cache to the people and groups you pick. It asks before it changes the central lakehouse's OneLake security roles: DefaultReader stops reading the cache's `access` schema, and a role named AccessCacheReaders reads it.

Refreshing needs write access to the central lakehouse. Reading the cache needs read access.

**Environments** and **Table access** ask Fabric directly, and changes there apply in Fabric right away.

- **Workspace roles** is a grid: one row per person, group or app, one column per environment's workspace, with their role in each (No access, Viewer, Contributor, Member, Admin). Change a role with its drop-down; the app asks before it changes anything. The **Worth a look** column flags entries that deserve a check. **Add a workspace** adds any other workspace to the grid. Listing roles needs Member or Admin on the workspace.
- **Give access** adds a person, group or app to a workspace with a role. Finding someone by email asks you once to let the app read your directory's names.
- **Table access in a lakehouse** lists the lakehouse's OneLake data access roles. These decide which tables Viewers, and people outside the workspace, can read; Admins, Members and Contributors read everything. **New role** and **Edit** choose what the role can read (everything, or some tables) and its members. **DefaultReader** is the role that lets everyone with read permission see every table. Saving or deleting a role rewrites the lakehouse's whole role list, so if someone else changed a role since you opened the page, nothing is saved and you're asked to open it again.
- **Who can read a table** answers that question for one table.
- If OneLake security isn't turned on for the lakehouse, the tab says so and offers **Open the lakehouse in Fabric** to turn it on (**Manage OneLake security**).

Changing roles needs workspace **Admin**. Needs `OneLake.ReadWrite.All`, `Workspace.ReadWrite.All` and Microsoft Graph `User.ReadBasic.All`.

## Data mesh

The **Data mesh** page (it was called Metadata) is where your metadata project lives. Metadata describes each table of your medallion layers once, in a file, and KratosForge builds and runs them. Its top tabs show the mesh for everyone; **Project** holds the open project's editor:

| Tab | What's there |
| --- | --- |
| Home (Preview) | The page opens here, even with no project open: **My team**, **On call** or **Organization**, the mesh as each of them sees it. Read only. See [Data mesh](data-mesh.md#home-the-mesh-for-who-you-are-preview) |
| Products (Preview) | Every team's published data products and each product's page, with no project needed. It was OneLake > Data products. See [Data products](#data-products-preview) |
| Requests (Preview) | Everyone's requests, with no project needed: **Waiting on you**, **Sent by you** and **Everything**. It was Platform > Requests; older links open it here. See [Data mesh](data-mesh.md#requests-preview) |
| Domains (Preview) | **Domains**: every domain and subdomain with its owners, projects and published products, and **Access groups**; platform admins import, add, edit and delete domains here. Works without a project. **Map**: your organization's domains and how they feed each other; open a domain to see its projects, their layers, products and readers, then a layer of the project on this PC to see each table's whole lineage, from its source files to the projects that read its products. Read only. See [Data mesh](data-mesh.md#from-domains-to-tables). **This project**: the open project's domain, the workspace each layer uses, and its owners and members; joining a domain is a request the domain's owners approve ([Members and joining a domain](data-mesh.md#members-and-joining-a-domain-preview)). The glossary is on **Platform > Set up > Glossary** |
| Project | The open project, in five groups. **Design**: Entities, Layers, Surrogate keys, Source systems. **Runs**: every run, with its steps, rows and problems. **Quality**: Tests, Table health and Exceptions. **Products and links** (Preview): the project's own data products and its links to other teams'. **Deploy**: Environments and promote, Semantic model. Project opens on the tab you last used |

Each entity page also has an **About** card (Preview): description, tags, classification, glossary terms and steward for the table and each column, saved in the catalog so anyone can write them without a Git change.

### Data products (Preview)

**Data mesh > Products** (OneLake's Data products tab opens it) lists every team's published [data products](data-mesh.md#data-products), from the platform's catalog, so anyone can find what other teams share without opening their project. Filter by domain or search by name, port or owner. Each row shows who owns it, who it's open to, its ports and how many projects read it, and what your open metadata project can do:

- **Link** opens the new link dialog for that product (it's open to everyone, to your domain, or its owner let your project in).
- **Request access** asks the product's owner to let your project link to it. They approve it on **Data mesh > Requests**; then **Link** shows here.
- **Linked**: your project already reads it.
- **Ask for data** (above the list) asks a domain's owners for a new data product; **Ask for a change** on a product's page asks its owner for a change. Accepting records the team's reply on **Data mesh > Requests**; nothing is built for you ([Ask for data](data-mesh.md)).
- **Follow** on a product's page tells you in Teams when a promote to Prod deprecates it, gives it a new version or changes a port's columns; **Following** filters the list to the products you follow. Linking a project to a product follows it for you ([Follow a product](data-mesh.md#follow-a-product-preview)).

Products works with no metadata project open. Select a product's name to open its page: its endorsement and business terms, checklist and who has access. For a product of the project open on this PC, **Edit definition** opens **Data mesh > Project > Products and links**, where its file is edited.

### Endorsement and business terms (Preview)

A data product's page shows its **endorsement** badge (Certified, Promoted or Master data) next to its name, as Fabric's admin tenant scan last read it, and when. Only Fabric administrators can read endorsement, so until one has scanned, the badge is a grey **Endorsement unknown**. A platform admin who is also a Fabric administrator chooses **Scan now**; after that, everyone sees the last scan's badge. To change endorsement, open the item's settings in Fabric: KratosForge can't set it.

**Business terms** list each term with **Open in Purview**. The product's owner or a platform admin chooses **Add a term**, types the term and pastes its address from Purview (an `https` link on `purview.microsoft.com` or `web.purview.azure.com`). KratosForge doesn't read Purview. The details are in [Endorsement and business terms](data-mesh.md#endorsement-and-business-terms).

### Tables, semantic models and reports (Preview)

A data product's page groups what it offers into **Tables**, **Semantic models** and **Reports**; each model and report has its own endorsement badge. The product's owner or a platform admin chooses **Add a model** or **Add a report**, picks a workspace and then the item: the product records only the item's id, type, workspace id and display name, and nothing in Fabric changes. **Remove** asks first. On **Data mesh > Products**, **Has tables**, **Has models** and **Has reports** find products by what they offer, and a product's badge is its semantic model's endorsement. Details in [Tables, semantic models and reports](data-mesh.md#tables-semantic-models-and-reports-preview).

### Direct Lake check

**Data mesh > Deploy > Semantic model** has a **Direct Lake check** card (Preview). Pick a model and a capacity, then **Check now**: it lists what would make the model fall back to DirectQuery, or fail, before anyone queries it. That covers tables past the capacity's Direct Lake limits, columns the Delta table no longer has, types Direct Lake can't read, and, when you tick the box, relationship keys that aren't unique and fact rows whose key has no match. It reads the model's definition from Fabric and each table's Delta log from OneLake and checks on this PC: it starts no Fabric job, though reading from OneLake uses capacity. Every check and the limits per capacity are in [Direct Lake check](direct-lake-check.md).

### Data dictionary

**Data mesh > Deploy > Semantic model** has a **Data dictionary** card (Preview): every table, column and measure of the layer's model with its description and classification from the catalog. **Export CSV** saves it to Downloads to edit in Excel; **Import descriptions** reads it back and shows every change before saving it to the catalog, as the About card does. A blank cell never erases text. **Update model** then writes the descriptions into the model. Details in [Data dictionary](data-dictionary.md).

### Approved exceptions

**Data mesh > Quality > Exceptions** lists every approved exception in `exceptions.toml`: the table, the rule, the table's owner, why it was approved, who approved it, and when it ends. An exception that ends within 14 days shows a banner on Data mesh and a notice on the bell, once a day, before the save gate starts failing again. One with no end date stays in force. How to write an exception is in the [metadata guide](metadata-guide.md#6-quality-checks-quarantine-and-the-four-gates).

The **Environment** picker in the top bar (Dev, Test or Prod) only shows on Data mesh pages. Everything on these pages, from runs to table health, is for the environment picked there.

The full walkthrough, from setting up a project to promoting to Prod, and a reference for every setting, is in **[the metadata guide](metadata-guide.md)**.

## Platform

The Platform page (Preview) is the central data team's setup: the landing zone, business keys, glossary, governance, audit and the platform's settings. Every other team sees what the platform offers there. **Platform admins** (the central team) create business key tables and landing zone sources, and decide requests on Data mesh > Requests; everyone else sees the same screens and sends requests. The platform itself checks who may do what, so hiding a button is only a convenience. Details in [The platform](platform.md#the-platform-page).

| Tab | What's there |
| --- | --- |
| **Overview** | Counts of domains, projects, key tables, data products and waiting requests; requests that need you; each environment's platform; the landing zone's sources and which are late; recent changes |
| **Landing zone** | The one list of source systems for every project, each with its id, and what landed in `LH_Landing` for the environment picked at the top right: its entities, newest files, when it last arrived and when it's expected, and which projects read it. Admins **Add source**; others **Request a source**. Projects pick theirs on Data mesh > Source systems ([One list of source systems](platform.md#one-list-of-source-systems-preview)) |
| **Business keys** | The platform's key tables (`sk.<name>`) with their business key and key counts. Admins choose **New key table**, which makes it in Dev, Test and Prod at once; others **Request a key table** |
| **Glossary** | Shared terms. Admins keep shared terms; a domain's owners keep their domain's terms |
| **Governance** | The organization's governance policies: search them, read each version, see where each applies and why ([Governance](#governance)) |
| **Audit** | Every change in the catalog and who made it |
| **Settings** | Where each environment's platform is ([Platform settings](#platform-settings)) and the platform's admins |

Domains and requests are on Data mesh: **Data mesh > Domains** and **Data mesh > Requests**. Older links to Platform's Domains and Requests open them there.

Whoever creates the platform in Fabric becomes its first admin. Admins add other admins on **Settings**; a platform always keeps at least one.

### Governance

**Platform > Governance** (Preview) holds the standards your organization defines once for every project: naming, schemas, data quality, classification, ownership, data contracts and deployment readiness. Platform admins choose **New policy**, edit the draft, and **Publish** it as a version that never changes afterwards; editing again makes a new draft. A policy applies where it's assigned: the whole enterprise, a domain, a project or one table. Domain owners can assign policies to their own domains. A policy marked mandatory can only be made stricter below where it's assigned. Open a policy and choose **Where it applies**, then **Explain** next to a project, to see each step that decided what it gets. A project can tighten or add policies in its own `governance.toml`. Details in [Governance policies](governance.md).

#### Policy exceptions

A policy's **Where it applies** tab lists its exceptions in force. One that ends within 14 days shows **Ends in N days**, a banner on Data mesh and a notice on the bell for whoever asked and the project it covers. A platform admin can choose **End early** and give a reason: it stops applying now, and the exception stays on record. Tick **Include ended** to see exceptions that have ended. Details in [Exceptions to a policy](governance.md#exceptions-to-a-policy).

### Best practices: checks and templates (Preview)

**Platform > Governance > Templates** lists reusable policy definitions: four **Built in** ones (table names, product owners, sensitive column labels and a data contract) and your organization's own (**Yours**). A platform admin chooses **Use** to make a policy draft from one, and any policy can be saved with **Save as template** from its page. Platform admins add templates, and can let domain owners add them too.

**Platform > Governance > Check this project** checks the open metadata project against the policies in force for it. **Check now** runs on this PC and lists what to look at, with a fix where a safe one exists (**Label it**) or a link to where to fix it. Check now stops nothing; a promote stops on a finding of a **Block** policy that no exception covers. A finding an approved exception covers says **Excepted until <date>: <why>**, and **Ask for an exception** sends the request. A data product's page (Data mesh > Products) also shows its **Checklist**. Details in [Governance policies](governance.md#best-practices-checks-and-templates-preview).

## Fabric

**Workspaces > Fabric portal** is Fabric's own portal, `app.fabric.microsoft.com`, inside the app. Links across the app (**Open in Fabric**, **Show in Fabric**) open their item here. Buttons along the top: **Open in KratosForge**, **Full screen**, **Back**, **Home**, **OneLake catalog**, **Monitoring hub**, **Reload**, and **Open in browser**.

- **Full screen** hides the app's top bar, left rail and tabs, so Fabric fills the window under a slim row of these buttons. **Exit full screen**, or Esc when the app (not Fabric) has the keys, brings them back. The app remembers it: the tab opens in full screen until you leave it. Keys you press inside Fabric go to Fabric, so the button is the sure way out.
- **Open in KratosForge** opens the warehouse or SQL analytics endpoint Fabric is showing on the app's own [Warehouse page](warehouse.md). On any other Fabric page it says to open one first. It reads only the address of the page Fabric shows.

It signs in with your Windows account when that is the same work account you use in the app, so there's usually no password prompt. It opens in the tenant of your app sign-in, which skips Fabric's email page. Otherwise sign in once; the app keeps that sign-in's cookies between launches, encrypted for your Windows account (DPAPI) in `%LOCALAPPDATA%\FabricDesktop\fabric-view-signin.bin`. Delete that file to make it forget. Links that leave Fabric open in your default browser.

**How this works**, from the title bar's info button, says that the app reads nothing from it and that changes you make there go to Fabric with Fabric's own prompts.

The portal used to have its own page in the left rail; that place now holds [Optimize](#optimize-preview). Type **Fabric** in the command bar (Ctrl+K) to open it from anywhere.

## Optimize (Preview)

What your Fabric estate could do better, what was fixed and what it could save, shared with everyone who uses the same optimization lakehouse. How the shared lakehouse works, what it stores and who can read it is in [Optimize and the central lakehouse](optimize/README.md).

- Until this PC is connected to an optimization lakehouse, the line under the title says results are from this PC only, and the buttons at the top right share them with your team: **Create a lakehouse** makes one in a workspace you pick, and **Connect to a lakehouse** lists the ones already in a workspace. Nothing on the page waits for it.
- **Overview:** how many problems were found and how many improvements are open, and the top recommended improvements. Before this PC is connected to an optimization lakehouse, **Recommended improvements** lists what this PC's last [Estate X-ray](#estate-x-ray-preview) scan found, most severe first, up to five, each with the X-ray's own fix (**Optimize**, **Vacuum**, **Open notebook**, **Open in Fabric**, **Review access** or **See cost**). **Optimize** and **Vacuum** ask first, as they do on Home, and a started fix shows as submitted. It reads the saved scan and never starts one; scan from Home.
- **Opportunities:** every recommendation with its state. **Accept**, **Mark applied**, **Mark verified**, **Dismiss...** (asks why) and **Reopen** are shared: everyone on the lakehouse sees the change, who made it and why. A dismissed recommendation stays dismissed when the problem is found again. **Show dismissed and verified** brings back the closed ones. With read-only access to the lakehouse you see everything but can't change it.
- **Cost and savings:** the capacity each run used in Fabric in capacity units (CU hours) and what that is at your price and, for runs on this PC, what they would have used in Fabric (estimated from Fabric runs of the same table, never from their time on this PC; Not known when the table hasn't run in Fabric in the last 90 days), over 7, 30 or 90 days, by item and by environment, with each environment's share of its capacity. Each figure has its evidence (Measured, Estimated, Scenario or Unknown); click a figure to see how it was worked out. A run whose time isn't known is left out of totals, not counted as 0. **Prices** sets your price per CU hour and currency, and when the [risk check](finops/risk-check.md) warns before a run. **Capacities** lists the capacity each environment is on and whether it is running, read only and marked stale after an hour; **Refresh** reads it again. See [Capacity of each environment](finops/capacity.md). **Measured usage** (Preview) imports a CSV exported from Fabric's Capacity Metrics app as measured CU, matches its rows to this app's runs and shows them beside the estimates. It also imports an Azure Cost Management cost details or FOCUS export and shows what each capacity was billed, in CU hours and money; see [Measured usage](finops/usage-import.md). See [Capacity and cost evidence](finops/cost-evidence.md). It works without a lakehouse. Fabric's Monitoring hub isn't shown here; it's under [Monitor](#monitor). This tab used to be Monitor > Cost.
- **Settings history** (Preview): what your capacity and tenant settings were at each read, and what changed between two reads, kept in the central lakehouse. **Read capacities now** reads the capacities you can see, and with **Include capacity overrides (administrators)** ticked, a Fabric administrator also reads the tenant settings each capacity overrides; **Read tenant settings (administrators)** needs a Fabric administrator. The first read asks before it creates the tables. Who changed a setting, and exactly when, aren't known. See [Settings history tab](optimize/README.md#settings-history-tab).

## Monitor

Monitor has a tab for each source: **KratosForge** and **Fabric**. Another source, such as Databricks or Snowflake, would be a tab beside them. Each tab's pages are on the switcher under the tabs, as on Data mesh.

**KratosForge** has three pages:

- **Runs:** everything this app ran or started, in every environment, on this PC and in Fabric: metadata runs, notebook table writes, notebook runs in Fabric, scheduled runs on this PC, table maintenance jobs, and pipeline runs on this PC ([Preview](#pipeline-runs-in-monitor-preview)). Filter by environment (All, Dev, Test, Prod) and by where it ran (Anywhere, This PC, Fabric). **Problems first** puts failures at the top. Each failed row says why. When a failed scheduled run on this PC couldn't post its Teams alert, the row says that too, and so does the schedule's last result in **Schedules**. The alert isn't lost: it waits on this PC (in `schedule_alerts.sqlite3`) and is posted, marked **Delayed**, after the next scheduled run here, oldest first; one still waiting after three days is dropped. If the metadata project can't be read (a broken `layers.toml`, say), Runs says so above the list instead of showing no metadata runs, Home says it couldn't check the metadata project, and the bell says freshness wasn't checked. Each metadata run says where it ran: **This PC**, **Agent** with the agent's name and server, or **Fabric**. Click a row for its details: a metadata run shows each step with rows and time, each check with how many rows failed, and the job it belongs to (the agent's job id, or the request id of a run in Fabric, which names its result file in `LH_Meta/Files/_fabric_desktop/results/`) and which try it was. Test and Prod runs show once you're signed in.
- **Schedules** (Preview): the schedules Windows starts on this PC for KratosForge, with how its last run went, when it runs next, and **Run now**, **On/Off**, **Open** and **Remove**. **Remove** asks first and says what stops: it deletes the Windows task, and past runs stay. To stop a schedule for a while, turn it Off instead. Schedules on this PC are started by Windows Task Scheduler even when the app is closed, as long as the PC is on and you are signed in to it. A pipeline schedule on this PC is added with `fabric-desktop pipeline schedule` for now ([pipeline schedules](orchestration/local-runs.md#schedules)). If this PC's schedules file is ever damaged (a crash mid-save, say), it is moved aside to `schedules.json.corrupt-<time>` in the app's folder, and a warning above the list says when and where. Schedules saved before then aren't listed, but Task Scheduler may still have tasks for them under the `FabricDesktop` folder. Their runs fail and don't show here. The moved-aside file has the old schedules if you want to add them again. The warning goes away once that file is moved out of the folder or deleted.
- **Operations** (Preview): the views for running workloads day to day. The dropdown on the view row picks one, and Operations opens on the last one you used. Each tab's one-line summary shows when you point at **Learn more**:
  - **Workloads**: everything you build and run, with its health, owner and what you can do with it. See [Workloads](#workloads-preview) below.
  - **Queue**: what is running, queued and next, on this PC, on agents and in Fabric, with who owns each item. See [Queue](#queue-preview) below.
  - **Agents**: every [agent](agent.md) that serves your environments, from the status each one reports. See [Agents](#agents-preview) below.
  - **Run as**: which identity runs your workloads in each place, the credential it uses, and where data goes. See [Run as](#run-as-preview) below.
  - **Incidents**: one record per problem in each environment, shared with your team and your agents. See [Incidents](#incidents-preview) below.

**Fabric** has two pages: **Monitoring hub**, Fabric's own Monitoring hub for everything in your workspaces, on the whole page, with **Back**, **Home**, **Reload** and **Open in browser** on the view row; and **Schedules** (Preview), the schedules in Fabric's own job scheduler, with how their last run went, **On/Off**, **Open** and **Remove** (which deletes the schedule in Fabric; past runs stay). Fabric schedules run on capacity even when your PC is off.

### Cancel a Fabric run (Preview)

A Fabric job this app started that is still running has **Cancel** on its row in **Runs** and **Cancel run** in the bell. Both ask first: the confirmation names the run and the workspace, and the safe choice, **Keep running**, has the focus. Cancelling asks Fabric to stop the job. Fabric stops it in its own time, so the row reads **Cancelling…** until Fabric reports **Cancelled**; the bell then shows **Cancelled**, which isn't counted as a problem. What the job already wrote stays written, and the capacity it used so far is still used. A cancelled run can't be resumed: run it again to start over. Cancelling works even when a policy turns runs in Fabric off. Jobs that this app didn't start aren't listed, so they have no Cancel here; stop them in Fabric's Monitoring hub on the **Fabric** tab.

### Pipeline runs in Monitor (Preview)

**Runs** also lists the pipelines you ran on this PC, newest first with everything else. A row says how the run ended: **Succeeded**, **Failed**, **Cancelled**, **Running**, or **Interrupted** when KratosForge stopped while it was going (it isn't resumed, because activities that were running may have done part of their work). A run where you gave the output of some activities instead of running them says how many were **mocked**, and when everything else succeeded it says **Succeeded with mocks**, never plain **Succeeded**. **Env** says **No environment**: pipeline runs on this PC aren't tied to one.

Click a pipeline run to see its activities underneath: each one's status, its type, how long it took, and for a ForEach how many iterations ran. A mocked activity says **Mocked**. When an If condition, Switch or ForEach failed, the row names the activity inside it that failed, with its error. The activities are read from that run's record on this PC when you open it, so a run that is still going shows where it had got to; **Refresh** reads it again. **Rows** shows a dash: pipeline runs don't count rows yet.

**Runs** also lists pipeline, copy job and dataflow runs in Fabric, from the run history of those items in the workspaces Monitor > Fabric > Schedules lists: pinned workspaces, your environments' workspaces and your notebooks' workspaces. They say **Pipeline run in Fabric**, how they were started (scheduled, by hand, or by another pipeline) and the workspace under **Ran on**. A run this app started is listed once. Copy job and dataflow runs say **Copy job run in Fabric** and **Dataflow refresh in Fabric**, and clicking one opens it in Fabric. Reading that history runs in the background, so Runs first says **Reading pipeline, copy job and dataflow runs in Fabric...** and fills in when it's done; after that it shows what it read until you press **Refresh**, and each row says **as of** when it was read. A run that was still going when it was read more than 10 minutes ago shows **Stale** instead of **Running**; with **Problems first**, Stale and Unknown rows come after real failures. Click a Fabric pipeline run to see its activities, read from Fabric when you open it and again only after **Refresh**: each one's status, type, start, how long it took and its error, with **Input** and **Output** to open. Secrets are removed from them, and a very long one is cut short. If your account can't read a run's activities, the row says **Not authorized to read activities**. **Open in Fabric** opens the pipeline in Fabric. All of this only reads: it starts no Fabric job and uses no capacity.

### Pop-out windows (Preview)

**Pop out runs** (top right on the KratosForge tab) and **Pop out hub** (top right on the Fabric tab) open that section in a window of its own, to keep on another screen while you build a notebook or work elsewhere in the app. Monitor then shows a band for it, with **Show window** (brings the window forward) and **Dock back**.

- **The runs window** has the KratosForge tab's Runs, Schedules and Operations with their filters and **How this works** panels. Runs reads the list again every 15 seconds, keeping your place in it (not while another panel or a dialog is open, or the window is minimized). **Details** on a run on this PC opens in the window, and a page opened from it (a workload, say) has **Back** at the top; a run in Fabric opens in the main window's Fabric portal.
- **The hub window** is the Monitoring hub with **Back**, **Home**, **Reload** and **Open in browser**. It is signed in as the docked view is (they share one sign-in on this PC), and it follows the same rule: only Fabric and Microsoft sign-in pages open in it, other links open in your browser (see [security](security.md), SH-12). **Sign out** closes it.

Each window has **Keep on top** and **Dock back**. Closing the window docks the section back too, and closing the main window closes them all. The app remembers each window's size and place on this PC (`pop-out-windows.json` in the app's folder) and whether it stays on top; a window last left on a screen that is no longer there opens in the middle of the main screen. A pop-out window starts nothing of its own: it uses the engine the main window started, and checks for no updates.

### Workloads (Preview)

A workload is something you build and run: a metadata entity (one per table, in each environment it is configured for), a notebook on this PC (with its Fabric item when it has one), or a pipeline, notebook or Spark job definition in the workspaces the app works in (the ones you pinned, your environments' workspaces and the ones your notebooks publish to). KratosForge never reads your whole tenant.

**Local pipelines** (Preview) are their own group: each pipeline in the repositories this PC opened, and each pipeline a schedule on this PC runs. Its runs are the ones started from the Pipelines page or by its schedules here. A run that mocked any activity shows **Needs attention** with "with mocks", because it doesn't show the whole pipeline works. Runs are kept by pipeline name, so when two pipelines share a name, only the runs their own schedules started count for each. **Run the schedule now** uses the schedule's saved parameters, mocks and notebooks, and asks first with the risk check, as for notebooks; a pipeline without a schedule here runs from its **Open pipeline** page. Local pipeline runs start no Fabric job, but steps that read or write a lakehouse use OneLake, and OneLake reads and writes use capacity. They open no incidents: incidents are kept per environment for metadata entities.

**How this works**, from the title bar's info button, says what it reads (and that it never reads your whole tenant), what you can change from it, and where ownership and the action log are kept. It folds to its title, and the app remembers that.

**The list.** Views across the top: **Needs attention**, **Mine**, **Unowned**, **On this PC**, **In Fabric**, **Production** and **All**, each with its count. **Group by**, a dropdown on the same row, picks Project, Owner, Environment or None. The search box filters by name, owner, environment, tag or state. Each row shows:

| Column | What it means |
| --- | --- |
| **Health** | **Failing** (its last run failed), **Late** (it missed its [freshness promise](metadata-guide.md#freshness-promises)), **Needs attention** (a decision is waiting, a run was cancelled, or a pipeline's last run used mocks), **Not observed** (KratosForge couldn't read it), **Healthy**, or **No recorded runs**. |
| **Owner** | The team or technical owner, and whether it was declared in the project's files or assigned on this PC. **No owner** when nobody is set, or when a template placeholder such as "me" is all there is. |
| **Deployed to** | Each environment and where it runs: This PC, an agent, or Fabric and its workspace. |
| **Last run** | How the newest recorded run ended. |
| **Can** | **Run** when you can start it from here, otherwise **Read only**. |
| **Observed** | How fresh the information is: **Live**, **Cached** with its age, **Stale**, **Signed out**, **No access**, **Fabric asked to slow down**, **Partly read** or **Offline**. |

The line above the list says what each source is and how fresh it is, for example "Cached 3 h · Fabric items in 4 workspaces (1 refused access)". **Refresh** reads them again.

**Not observed is not failing.** When your sign-in has expired, a workspace refuses access or Fabric asks the app to slow down, the workload shows **Not observed** with the reason. That says nothing about the workload itself, only that KratosForge couldn't read it. The last reading it had is shown as **Cached**, and becomes **Stale** after an hour.

**Mine** means you are its team, technical owner, business contact or support contact, declared in the project's files or assigned on this PC. It never means "you ran it last".

**The workload page.** Click a row to open it. It shows:

- **Health and readiness:** each finding with where it came from (the run log, Fabric's job history, a freshness promise) and what to do next: **Open run**, **Retry**, **Compare and promote**, **Sign in**, **Assign** or **Link**.
- **Ownership:** team, technical owner, business contact, support contact, criticality and tags. **Edit** changes them on this PC; each change is recorded with your name. A role set in the project's files (an entity's `owner`) is changed there, not here.
- **Deployments:** each environment with where it runs, as whom, where its tables are, which version it runs (and **Behind** when the project has moved on), its schedules and latest run.
- **What changed:** runs, promotions, edits to its definition, actions taken from this app and ownership changes, newest first.
- **Who is affected:** what reads from it in the project, and the data products it feeds with their consumers. Only declared links are listed.
- **Capacity use** (Preview): what the runs this app started in Fabric used in the last 30 days. Runs Fabric started on its own schedule, or that someone started in Fabric, are in **Recent runs** but aren't counted here; Optimize > Cost and savings shows the capacity's imported usage. A run counts with its measured CU when an imported Capacity Metrics row matched it (**Optimize > Cost and savings** imports them), otherwise with the app's estimate; a run with neither is left out and the total says **Incomplete**, never counting it as 0. Each figure says **Measured** or **Estimated**; click it to see how it was worked out. A match is likely, not proven, because exports have names and times, not job ids. Runs on this PC start no Fabric job, but their OneLake reads and writes still use some capacity, which isn't counted here. It reads only what this PC already holds.
- **Recent runs** and **Actions from KratosForge**.

**Actions say what they will do first.** **Retry**, **Run** and **Cancel the run** open a confirmation that names the environment, where it runs, as whom, what it writes and any warning (the last failure, or an environment that runs an older version). Runs go through the same paths as Data mesh > Run, Run in Fabric and Run now on a schedule. Each action is recorded as **Submitted**, then as **Succeeded** or **Failed** once the run records say how it ended. A cancel counts as succeeded only when Fabric reports the job as cancelled.

A notebook is linked to its Fabric item when you publish or run it in Fabric from this app, and the link survives renames. A notebook that only shares a name with a Fabric item is shown as linked "by name"; **Link** keeps it by its id. **More > Unlink** removes the link.

Ownership, criticality, tags, links and the action log are kept on this PC in `workloads.sqlite`, next to your other app data. They aren't shared with your team yet.

**On the platform's audit.** When there's a platform ([The platform](platform.md)), each action and change made from a workload page is also written to **Platform > Audit**: the action as **Submitted**, then as **Succeeded**, **Failed** or **Cancelled** once the run records say how it ended, and each owner, criticality, tag or link change with what it was before and after. Who made it is the signed-in person the platform sees, not what the app says. The environment, where it ran, as whom and the target go with it; secrets in an error are removed and each value is cut to 500 characters. If the platform can't take a line (you're signed out, or a platform in Fabric hasn't been updated yet), this PC keeps it, sends the lines in order later, and **Actions from KratosForge** says how many wait and why. Without a platform nothing is sent and no platform is made. The action itself never fails because its audit line couldn't be sent.

**On Home**, **Your workloads** (Preview) shows what you own by health, the ones that need attention with **Open**, and how many workloads in your workspaces have no owner, with **Review**.

### Incidents (Preview)

An incident is one record per problem in an environment: a table whose last run failed, a run waiting for a decision, a late table, a missed [reliability objective](metadata-guide.md#reliability-objectives-preview), an agent that stopped reporting, or an environment that couldn't be read. An environment that isn't set up yet (no workspace assigned) isn't a problem, so it opens no incident; **Check now** says it isn't set up. The list lives in the environment's `LH_Meta`, so everyone who can read the environment, and every agent that serves it, sees the same incidents. Two people or agents seeing the same failure open one incident, not two.

**How this works**, from the title bar's info button, says what the page reads, which of its actions write to the shared list, when the app posts to Teams, and that an environment that can't be read is never counted as a failing workload. It folds to its title, and the app remembers that.

**The list.** Pick the environment (each chip shows its active count, or why it can't be read) and **Active**, **Mine** (incidents assigned to you) or **Resolved**. Each row shows the state (**Open**, **Acknowledged**, **Recovered**, **Cleared** or **Resolved**), the problem with its evidence and a **Likely** category for failed runs, when it opened and who opened it, how often it was seen, its owner, and whether it was escalated.

**Who keeps it current.** The line above the list names the agents that check the environment on a timer (from their status records) and whether they are live. When no running agent checks it, it says so: the app checks it itself every 10 minutes while it's open, and posts new incidents to its own Teams webhook if one is set (**Settings > Notifications**). **Check *environment* now** checks at once and never posts to Teams. Agents post to the routes their admin set ([agent guide](agent.md#incidents-preview)).

**Likely** is a hint matched from the error text (Access, Throttled or capacity, Network, Table busy, Source missing, Code or schema, Data quality, Code, or Unrecognized) with whether a retry alone usually helps. The run's own error is the evidence.

**Acting on one.** **Acknowledge** says someone is on it and stops escalation. **Assign** sets the incident's owner (the workload's own owner stays as it is). **Add note** adds a note everyone sees. **Resolve** closes it; it opens again only on new evidence, such as another failed run. Each one says what it changes before it does it, is recorded with your name and time, and never runs anything or changes data.

**The incident page** shows what happened with its evidence and **Open run**, who is told (owner, criticality, and for each agent which Teams route it posts to, its quiet hours and when it escalates), and the history: opened, escalated, acknowledged, notes and resolved.

#### Who's affected (Preview)

For an incident about a metadata table, the incident page shows **Who's affected**: each published data product whose ports in that environment carry the table, the projects linked to those ports, and the people to tell, with why (owns the product, linked a project to it, or follows it). It is one hop: what those projects build from the data isn't traced further. Names that aren't emails are listed but can't be sent a message. Who follows a product is personal data, so its followers are listed only to its owners and platform admins; anyone else sees that they were left out, and the product's owner can tell them.

**Tell them** posts one Teams message through the platform's Teams routes (**Settings > Notifications**, kind *Incident reaching data products*) naming those people and products. The message has the incident's title and the products' names, never its notes. A note on the incident records who was told and when. Telling again only posts when someone new is reached. With no platform Teams route set, nothing is posted: **Copy emails** copies the addresses so you can tell them yourself.

**On a workload's page**, **Incidents** lists its active incidents in every environment.

**Numbers, priorities and SLAs.** Every incident now gets a number (`INC-PROD-0042`), a priority from P1 to P4, a timeline, and SLA targets for acknowledging and resolving it; people can also report a problem the app can't see. The list doesn't show these yet. How they work, and how IT sets its own SLA targets: [Incidents and your service desk](incidents.md).

### Agents (Preview)

An [agent](agent.md) is KratosForge running on your own server. Every minute or so it writes a status record to each environment it serves, in that environment's `LH_Meta`. Monitor > KratosForge > Operations > Agents reads those records. It never reaches the agent's server, and nothing on it starts, stops or changes an agent.

**How this works**, from the title bar's info button, says what the page reads, that it changes nothing and stores nothing, what an agent is, the four steps to set one up (each linked to its section of the [agent guide](agent.md)), and what is coming: shared schedules and takeover (Preview, off by default). It folds to its title, and the app remembers that.

**The list.** The line above it says, for each environment, how many agents serve it and when it was read, or why it couldn't be read (for example, no access to its `LH_Meta`). Each row shows:

- the agent and its server;
- its state;
- the environments it serves;
- what it is running and how much is queued;
- its next scheduled run;
- how its last 10 jobs went, with the newest error;
- whether it checks [incidents](#incidents-preview);
- its version compared with this app.

**State.** An agent is **Running** while its reports arrive on time. It shows **Stopped reporting** once the time it promised to report again has passed. That means it stopped without saying so: a crash, a power cut or a lost network. It shows **Stopped** when it shut down cleanly. For a stopped or silent agent, everything shown is its last report as it was, labelled with when it was written, so its jobs show as unknown rather than as running.

**Scheduled in two places.** Each agent and this PC keep their own queue, so when two of them schedule the same table, both run it. Table leases only stop them writing at the same moment. Above the list, **Scheduled in two places** names each table that is scheduled both on an agent and on this PC, or on two agents, in the same environment, with both schedules. Give each table's schedule to one place; **Review schedules** opens this PC's schedules. It changes no schedule itself. A cleanly stopped agent isn't counted. An agent that stopped reporting still is, because it runs a missed schedule when it is back. An older agent that doesn't say which tables its schedules run is listed as one that can't be compared, never as running every table.

**The agent page** shows its owner and how it signs in (a service principal, a managed identity or a user's cached sign-in) when its `agent.toml` says so, and, for each environment:

- what the agent runs: pipeline jobs and which entities, how many at a time, and whether backfill is allowed;
- its schedules, the tables each runs, and when each runs next;
- what it was doing;
- whether it checks incidents.

It also lists its last jobs. When the agent has stopped reporting or is older than the app, **What you can do** says what to check on its server. A stopped agent runs each missed schedule once when it starts again, and picks up any job it was running. An incident about an agent that stopped reporting has **Open agent**.

### Queue (Preview)

Each place work runs has its own scheduler and queue: Windows Task Scheduler on this PC, each agent's own queue, and Fabric's scheduler. Monitor > KratosForge > Operations > Queue puts them in one list and says, on every row, which one owns it. It never starts, stops or reorders anything. **How this works**, from the title bar's info button, says what it reads, that it changes and stores nothing, and what **Unknown** and **Missed** mean.

The line at the top says how each source was read. **This PC** is its schedules. **Agents** come from their status records. **Fabric** is the jobs this app started, as last checked, and the Fabric schedules that Monitor > Fabric > Schedules last read. The Queue page makes no Fabric call of its own. Until Fabric schedules has read Fabric's schedules, the line says so, with a link.

**Running and queued** lists what each agent is running and how many jobs it has queued, and the Fabric jobs this app started that haven't finished. An agent's status record counts its queued jobs but doesn't list them. For an agent that stopped reporting, what it was running shows as **Unknown**, from its last report; it picks those jobs up again when it is back.

**Next 24 hours** lists each schedule due in the next day, with when it runs. A schedule that an agent should have run while it was down shows as **Missed**; the agent runs it once when it is back. A [shared schedule](agent.md#shared-schedules-preview) is one row that names every agent sharing it, because each due time runs once on whichever claims it; it is **Missed** only when none of them is reporting. On an agent's page, such a schedule is tagged **Shared**. A Fabric schedule says how often it runs, not when it runs next, so it is listed with its pattern, after the rest.

A run Windows Task Scheduler started on this PC isn't listed while it runs; it shows in **Runs** when it finishes. Fabric work that this app didn't start isn't listed either. It is in Fabric's own Monitoring hub, on Monitor's **Fabric** tab. The page doesn't poll your tenant.


### Run as (Preview)

Monitor > KratosForge > Operations > Run as lists each place your workloads run and says which identity runs them there, the credential it uses, and where data and credentials go. It only reads: nothing here signs in, renews a certificate or changes access. Renewing a certificate or fixing an identity happens in Microsoft Entra and on the agent's server. **How this works**, from the title bar's info button, says what it reads, that it changes and stores nothing, and what **Unknown** and **Not visible** mean.

- **This PC** and **Fabric jobs from this app** run as you. If you are signed out, they say so. Your organization's policy is shown where it applies: how long local run results are kept, which tenants you may sign in to, and whether this app may start jobs on Fabric capacity.
- **Each agent** runs as the identity in its `agent.toml`: a service principal, a managed identity or a user sign-in. The app never sees its ids. Agents report facts about their credential in their [status record](agent.md#status-records): a certificate's end date, and when their last token request worked or failed. An agent never publishes the credential, its path or its thumbprint. A secret's or a managed identity's expiry isn't visible to the agent, so it isn't shown. An older agent that doesn't report these facts shows **Unknown**.
- **Fabric schedules** on items in your environments' workspaces run as their owner, as Monitor > Fabric > Schedules last read them. Fabric doesn't say when the owner's access lapses, so that shows **Not visible**.

The banner at the top lists what to look at: a certificate that has expired or ends within 30 days, and an agent whose last token request failed. For an agent that stopped reporting, its row shows its last report, as it was.

## Source control (Preview)

**Source control**, at the bottom of the left rail, is Git in the app: clone a repository from GitHub, Azure DevOps or any Git server, see what changed (inline, side by side, or cell by cell for notebooks), stage and commit (a whole file, or one block of changed lines at a time), make and switch branches, fetch, pull and push, and read the history. The top bar shows the branch you're on, how many commits are ahead or behind, and a dot when there are uncommitted changes. Commits are checked for secrets first, and nothing that discards your work happens without asking. It uses the Git installed on your PC and its sign-in (Git Credential Manager or your SSH agent). Pushing never changes a Fabric workspace. Notebooks, data mesh projects, platform definitions and capacity definitions each get their own kind of repository, and the list is grouped by kind. Notebooks can be saved in a notebooks repository, where they find their lakehouse for each environment from that repository's locations. A platform repository holds the platform's definitions as files: compare them with what each environment has deployed, export the platform into them, and deploy committed changes after one confirm. The whole page is described in [Source control](source-control.md).

### Capacity repository

A capacity repository holds `capacity.toml`: the capacities there should be, the size, region and state each should have, and the workspaces each should hold. Its **Capacity** tab is a drift report. It compares the file with the capacities and workspaces Fabric shows you, and for each capacity it says **same**, **differs** (which setting, or which workspace is elsewhere or not in the file), **not in Git** or **not visible to you**. It only reads: it never resizes, pauses, resumes or assigns a capacity, and it starts no Fabric job. **Write Fabric's state to capacity.toml** replaces the file with what Fabric shows, after you confirm, as a change to review and commit. Capacities and workspaces you can't see stay in the file as they are. See [Capacity repository](source-control.md#capacity-repository).

## Profile (Preview)

**Profile**, in the account menu (top right), is a private record of work KratosForge verified: notebooks that passed Validate with a clean run. It shows your level and achievements (for fun, not a skill rating), skills as evidence coverage, and a **Professional report** you can save as Markdown for a review. Recording is on from the start and stays on this PC; turn it off in **Profile > Settings**. The whole page is described in [Profile](profile.md).

## Settings

Open Settings from the gear at the bottom of the left rail, or from the account menu. Settings has two tabs: **General**, below, and **Integrations** (Preview), which connects [external AI tools](#external-ai-tools).

| Section | What's there |
| --- | --- |
| **Account** | Who you're signed in as and **Sign out**. When sign-in uses your organization's own app, **Admin approval** copies the link a Microsoft Entra admin opens once to approve it. |
| **Updates** | Stable or Preview channel. See [Updates](#updates). |
| **Preview features** | Show or hide the features tagged Preview. Promote, Sync keys, the variable library, quarantine Replay and the risk check before a run are core steps: they always show, with their Preview tag. |
| **Platform** (Preview) | A link to **Platform > Settings**, where each environment's [platform](platform.md) is created, joined or repaired. See [Platform settings](#platform-settings). |
| **Optimize lakehouses** (Preview) | The optimization lakehouse this PC shares results through: **Switch to**, **Check** (your access and its schema) and **Disconnect...** (its data stays). **Advanced** has this installation's random ID, **New installation ID**, and an optional name everyone on the lakehouse sees. See [Optimize](optimize/README.md). |
| **Usage data** (Preview) | Off unless you turn it on. Once a day it sends how often each action ran, failed and took, never your data or names. **See what's sent** shows the exact text. See [Usage data](telemetry.md). |
| **Notifications** | **Teams alerts**: post new incidents (late and failed tables, missed objectives, agents that stopped) to a Teams channel, unless an agent already checks that environment. See [Freshness promises](metadata-guide.md#freshness-promises). **Platform notices** (Preview): Teams channels for platform requests and broken shortcuts. See [Platform notices](#platform-notices-preview). |
| **Help and about** | This guide, **Report a problem**, the **Guide for IT admins**, the build, the engine's Python version and where the organization settings file is. |
| **Advanced** | Folded until you open it: the App (client) ID, the notebook process and its limits, OneLake writes, and the SQL query limits. See [Advanced settings](#advanced-settings). |

Anything your organization has set shows "Set by your organization's policy" and can't be changed there.

### Platform notices (Preview)

**Settings > Notifications > Platform notices** sends platform requests waiting on someone, their decisions and withdrawals, shortcuts that break or are fixed, data products changed in Prod or deprecated, incidents upstream of your data, and policy exceptions about to end to Teams channels ([Notices to Teams](platform.md#notices-to-teams-preview)). Each change is posted once.

- **Add a route** names a channel, takes its Teams webhook, and optionally picks which kinds of notice and which domains it posts. A route named `default` gets everything no other route takes. **Test and save** posts one test message first and saves only if Teams takes it. A route's webhook is shown only as its host. Leave the box empty when editing to keep the address it has. **Remove route** asks first.
- **Quiet hours** (this PC's clock, like `22:00-07:00`) hold notices until they end. **Send during quiet hours anyway** lets warnings, or everything else, through.
- When notices haven't reached Teams, because quiet hours hold them or Teams didn't answer, the section says how many. They wait on this PC for up to three days.

The bell's **Platform** filter lists the requests still waiting and shortcuts still broken that this PC's notices know about, each with **Open in Data mesh > Requests** or **Open in Shortcuts > Health**, plus a line for notices that haven't reached Teams. Another PC or Fabric may already have settled some. A request's reason never shows there or in Teams. The filter appears once a route is set or a notice is open.

The requests and shortcut pages don't send notices yet, so routes set now start posting when they do.

### Advanced settings

At the bottom of **Settings > General**, folded. Click **Advanced** to open it; it stays open or closed on this PC as you left it. Most people never need it.

| Group | What's there |
| --- | --- |
| **Sign-in** | The **App (client) ID**. Leave it empty: KratosForge then uses Microsoft's own Fabric sign-in and nobody registers an app. Only paste an ID if your organization gave you its own app ([Permissions](permissions.md#using-your-own-app-registration)). When an ID is in use, a note says so, where it came from and how to go back; if you set it here, **Use Microsoft's sign-in** clears it. |
| **Notebooks** | **Run notebooks in their own process** (Preview) and, with it on, the **Cell time limit** and **Notebook memory limit**. |
| **OneLake writes** | How notebooks on this PC write tables: **New notebooks write** (here, then upload in the background; or straight to OneLake, for a fast upload line), **Compression** (Zstandard or Snappy) and **Skip unchanged tables**. Each notebook can choose its own under **Runs on**. **Files on this PC** shows what local writes hold (normally nothing: a write is deleted once it has uploaded) and opens the folder in File Explorer, along with any TPC-H data a run ended mid-way left in the temp folder. **Delete left-overs** deletes all of it after you confirm (your tables in OneLake aren't touched); it's refused while a notebook is running, and what a run still going in another process (a scheduled run) wrote is left, as is any file in use; the notebook's **Runs on** menu has the same **Open the folder of local writes**. See [Writing to OneLake](architecture.md#writing-to-onelake). |
| **SQL queries** | Rows per page, time limit, memory, queries at once, how long results are kept, and the **Temp folder**. See [Running SQL queries on this PC](sql-execution.md#limits). |

When your organization's policy file sets one of these, or the `FABRIC_DESKTOP_CLIENT_ID` environment variable sets the App ID, the field shows the value it uses, can't be edited and says which: "Set by your organization's policy" or "Set by the FABRIC_DESKTOP_CLIENT_ID environment variable". To change an ID the variable sets, remove the variable (`setx FABRIC_DESKTOP_CLIENT_ID ""`) and restart KratosForge.

### Layout

**Settings > Appearance > Layout** sets how much every page shows. Nothing is removed by any of these:

- **Explanations.** **On request** (the default) keeps the help out of the way: each page's one-line explanation and its **How this works** panel, which says what the page reads, what it changes and where results go, open together as a drawer from the info button in the title bar. Escape or the button closes it. The button shows only on pages that have help. **Beside the page** puts the explanation under the title and the panel in a column on the right, as before. Confirms before a change always say what they change, whichever you pick.
- **Preview marks.** **Tags** (the default) marks features still in preview with a Preview tag. **Quiet dots** shows a small orange dot instead; hover it for what it means. Page titles keep the full tag, so you always know when a whole page is in preview.
- **Density.** **Comfortable** (the default) or **Compact**, which tightens tables, lists, toolbars and cards.
- **Learning guidance** (Preview). **Expert**, **Professional** (the default) or **Guided**: how much explanation the best-practice panels and Learn show beside your work. See [Learn](learn.md#guidance).

### Motion and effects

**Settings > Appearance > Motion & effects** sets how much the app moves. **Visual intensity** is
**0 Off** (nothing moves), **1 Minimal** (quick page and panel transitions only), **2 Refined**
(the default: small sparks, embers and the wolf on real progress) or **3 Expressive** (the same
moments with more character). Switches turn off one family of effects at a time: wolf
animations, forge sparks and running indicators. Changes apply
at once and are kept. **Preview effects** plays each effect on a sample, and runs a three-step sample
pipeline on this PC to show the pipeline flow. Progress, results and errors look the same at every
level. See [Motion and effects](forge-animations.md) for every effect and when it plays.

### Platform settings

On **Platform > Settings**. Each environment shows whether it uses a Platform workspace in Fabric or, until there is one, a file on this PC. **Create** makes the Platform workspace (LH_Landing, LH_Meta, SQL_Platform, UDF_Platform, VL_Platform) and reports each step; **Finish or repair** runs the steps again; **Join existing** picks a Platform workspace the central team already made; **Forget** stops using it on this PC without deleting anything. Details in [The platform](platform.md).

### External AI tools

**Settings > Integrations** (Preview) lets AI tools you already use, such as Claude Code, Claude Desktop, Codex, Cursor, VS Code with GitHub Copilot or any MCP client, read your projects and, with your approval, run work on this PC. KratosForge has no AI of its own: no chat, no model to pick, no AI key. It sends nothing to any AI service; your tools ask it for things. Claude Code has been partly tried with it. The others haven't been tried yet, so they are marked **Untested** ([compatibility](product/ai-integration-compatibility.md)).

1. Turn on **Let AI tools connect**. It's off until you do, and your organization can keep it off.
2. **Add a tool**: pick it, name it, and choose what it may do. **Read metadata** (names, structure, schemas, checks, no rows) is always included. **Read data** reads rows. **Run code on this PC** runs Python and notebooks, and asks you every time.
3. Paste the setup it shows into your tool once, then **Test connection**. The setup uses a command whose path stays the same after updates.

When a tool asks for something that needs you, a prompt opens over whatever page you're on, showing the tool, what it wants to do, on what, and any code in full. **Allow once** lets that exact request through one time; **Deny** refuses it. A tool can't approve anything itself, and text it sends can't either. Requests nobody answers are dropped after 5 minutes. KratosForge must be open for you to see them.

| Part of the tab | What it's for |
| --- | --- |
| **How tools connect** | The MCP command tools start, and the **Local HTTP API** switch for tools that call `http://127.0.0.1:47615` instead (this PC only, with a token per tool). |
| **Connected tools** | Each tool with what it may do and when it last asked. **Edit** changes its permissions, limits it to some workspaces (it then can't run code, checks a run's risk only for workloads deployed in those workspaces, and sees only the notebooks, proofs, receipts and runs in those workspaces) or makes a new HTTP token. **Revoke** stops it at once. |
| **Extensions** (Preview) | Extra capabilities tools can use, each off until you turn it on. The built-in **Notebook outline** lets a tool read a notebook's headings, functions and classes without its code. A tool still needs the permission an extension asks for, and anything that runs code still asks you. Tools that are connected see the change within a few seconds. Your organization can limit which extensions can be turned on. |
| **Recent activity** | Every request each tool made, refused ones included: what it asked for, what was decided (allowed, approved by you, refused, denied by you, not answered) and how it ended. A call you approved shows **started** while it runs; if the tool or KratosForge stopped before it finished, it stays **started** (or **unfinished**), so an approved call never goes missing. Code, queries and table data aren't kept. **Check the trail** confirms no line was changed or removed. The full trail, for your IT team, is in `%LOCALAPPDATA%\FabricDesktop\integrations\audit`. |

Code a tool runs gets a process of its own that's ended afterwards. It can read the attached lakehouse but can't write tables or get other sign-in tokens, and its output has anything that looks like a password or token removed. SQL from a tool can only read. Setup for each tool, workflows and instructions to paste into it: [Connect your AI tool](ai/README.md). Details for developers and IT: [AI integration](architecture/ai-integration.md), [security](product/ai-integration-security.md).

### Run cells from VS Code

**Settings > Integrations > VS Code** (Preview): turn on **Let VS Code run notebooks**, then pick KratosForge as a notebook's kernel in VS Code. KratosForge asks whether to pair that window, with a code VS Code shows too; check they match. A paired window runs cells in the notebook's own session on this PC without asking each time, the same session the notebook uses here. Writing a table or file to OneLake from it asks here every time, and one **Allow once** covers that table for the rest of the cell. Choose how long a pairing lasts (until KratosForge closes, 8 hours or 7 days); the tab lists paired windows and **End** stops one at once. Turning the switch off ends them all. AI tools such as Copilot don't pair: they keep asking before every run. More: [Run cells from VS Code](vscode.md#run-cells-from-vs-code).

## Updates

KratosForge checks for an update each time it starts and installs it before opening. The first start on the new build shows the forge splash, the same animation that announced the KratosForge name, with what's new since the build you ran before (up to five changes). **Enter the forge**, Enter or Escape closes it, and **See everything new** opens What's new. It follows **Motion & effects**: with motion set to Off or Minimal, or reduced motion turned on in Windows, it shows still, and with **Forge sparks** off the emblem still strikes but no sparks fly and no embers rise. To get a small What's new window instead, clear **Show this after every update** on the splash or turn off **Show the forge after an update** in **Settings > Updates**; the command palette's **Show the update splash** shows it again.

**What's new** lists what changed: the build's own notes, then everything that reached the app in the 30 days before that build, newest first. The 10 newest show; **Show more** lists the rest, each with a link to its pull request. A link at the bottom opens KratosForge on GitHub. You can open What's new again from the account menu.

![What's new, with the 10 newest changes and Show more](images/whats-new.png)

There are two channels:

- **Stable** (the default): builds that have been checked and released.
- **Preview**: every new build as soon as it's ready. For testers; things may change or break.

To update without waiting for the next start, choose **Check for updates** in the account menu (or in **Settings > Updates**). It shows your build next to your channel's newest one. If that one is newer, **Install and restart** downloads it, saves the open notebook, closes KratosForge and starts the new build; running cells stop. If there is nothing newer it says you're up to date. **Download manually** opens the channel's release page on GitHub, where you can download `kratosforge.exe` and run it in place of the one you have.

### A new build while the app is open (Preview)

If you keep KratosForge open for days, it still tells you when a new build is out. Every 4 hours it reads your channel's version file, the same check it makes at startup; nothing else is sent. When your channel has a newer build, a banner below the top bar says so, and the bell gets an **Update** entry.

- **Restart to update** saves the open notebook, downloads the build, closes KratosForge and starts the new one; running cells stop. If the update fails you stay on your build, and the banner says why.
- **Later** hides the banner for that build. The bell entry stays, and the next newer build shows the banner again.
- **Release notes** opens the build's release page on GitHub.

On a PC where your organization rolls out builds (`"auto_update": false` in the policy file), the banner only says the build is out and to ask IT; it offers no install. To stop the banner, turn off **Tell me when a new build is out** in **Settings > Updates**. It also stays off while preview features are hidden.

A channel change takes effect the next time KratosForge starts. More detail, including where files are kept, is in [install.md](install.md#updates).

## Preview features

Features marked **Preview** work but haven't been tried on many Fabric tenants yet, so they may change. To hide them, turn off **Settings > Preview features**. A few are core steps, such as Promote and the [risk check before a run](finops/risk-check.md): they always show, with their Preview tag.

The one list of Preview features, with where each one is, is on the [documentation home](README.md#what-is-tested-and-what-isnt), which also says what is tested and what isn't.

## Report a problem

Account menu or Settings > **Report a problem**. Describe what happened; KratosForge adds its build, channel, the engine's Python version, whether you're signed in, the page you're on and the recent errors from Activity (the bell). It never adds your data, file contents or passwords, and it takes out email addresses (a guest's sign-in name too), your tenant's name in `onmicrosoft.com` and `sharepoint.com` addresses, the site or team name in a SharePoint address, the owner in a OneDrive web address, your organization's name on a OneDrive folder, your Windows user name in paths, and tenant, workspace and item ids. Your organization's own domain (such as `contoso.com`) isn't recognized, so read everything it adds before you send.

**Send** copies the report and opens your organization's support contact if it has one, or a new GitHub issue where you paste it. **Copy only** just copies it.

Ideas and feature requests go to the ideas forum instead: see [Submitting ideas](ideas.md).

## For IT admins

Setting KratosForge up for an organization, so nobody has to type an app ID: see [Configure Azure and Fabric](azure-setup.md) and [Rolling out to your organization](permissions.md#rolling-out-to-your-organization). For teams sharing projects and environments, see [Working across teams](teams.md).

### Audit trail

Every time someone runs or changes something in the app (a notebook cell, a SQL query, a metadata run, a publish, a promote, a schedule, a shortcut, a vacuum, a setting), the engine adds a line to `%LOCALAPPDATA%\FabricDesktop\audit\audit-YYYY-MM.jsonl`. On a server, the headless agent writes to `audit\` in its home folder: one line when a run is queued (`agent_enqueue`), requeued (`agent_requeue`) or the queue is pruned from the command line (`agent_prune`), and one when each job finishes or fails (`agent_job`, with the tables, rows written and error). Reading, listing, previewing and waiting for results aren't recorded.

Each line says:

| Field | What it holds |
| --- | --- |
| `at`, `seconds` | When it started (UTC) and how long it took |
| `action`, `outcome` | What was done (`meta_promote`, `run_sql`, `xray_vacuum` ...) and `ok` or `error`, with the error text |
| `who`, `tenant` | The account signed in to the app, and its tenant |
| `host`, `os_user`, `app`, `version`, `compute` | The PC and Windows account, desktop or agent, the engine version, and where it ran |
| `target` | What it was done to: workspace, lakehouse, environment, entity, notebook name, ids |
| `prev`, `hash` | A SHA-256 chain linking each line to the one before. The app, scheduled runs and the agent take turns writing (through `audit\.lock`), so they never break the chain by writing at the same moment |

Code, SQL text, cell contents and data are never recorded; they show as `(given)`. Tokens, keys and passwords are taken out of errors.

To read it, or to check that no line was changed or removed since it was written:

```powershell
fabric-desktop audit --limit 20                      # the newest 20 lines
fabric-desktop audit --action meta_promote           # only promotes
fabric-desktop audit --verify                        # exit code 1 if the chain is broken
fabric-desktop audit --folder D:\agent\audit --verify # an agent's trail
```

Each write also records the newest line and the line count in `audit\head.json`, so `--verify` also reports lines deleted from the end of the trail, which the chain alone can't show. If lines go missing before the next write, `head.json` keeps a note of it, so later lines don't hide the gap. Someone who can edit the folder can edit `head.json` too. Each audited action writes `head.json` to disk (a small extra write and flush next to the line itself). Once you've looked into a reported gap (for example, the files were restored from an older backup on purpose), clear it by deleting `head.json`: the next write starts a new one from the trail as it is, and `--verify` stops reporting the gap.

The files are plain JSON lines, so a log shipper (Azure Monitor agent, Splunk, Fluent Bit) can collect them. The trail is evidence, not a lock: someone who controls the PC can delete the whole folder, which `--verify` can't see. If that matters for you, ship the files to a store the PC can't change.

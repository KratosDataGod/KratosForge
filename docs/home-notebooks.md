# Recent notebooks, Run several and pinned items

Home's **Recent notebooks** shows where each notebook lives and how it last ran. You can tick several and run them at once (Preview), and pin the notebooks and Fabric items you use most so they sit at the top of Home (Preview).

## What each notebook shows

| Column | What it is |
| --- | --- |
| **Name** | The notebook. Click the row to open it. In a notebooks repository, the line under the name says which one. |
| **Workspace** | Where the notebook publishes: the workspace picked for it, else its lakehouse's workspace. Click it to open the workspace in Fabric. **Not published** means it has neither yet. |
| **Lakehouse** | The lakehouse attached to the notebook, or **None**. |
| **Runs on** | Where **Run all** runs it: **This PC**, **Fabric**, or the environment's choice, shown as "This PC (environment)" or "Fabric (environment)". |
| **Last run** | How its last background run ended, and when: a schedule on this PC, **Run from Home**, or **Run in Fabric**. Cells you run in the open notebook aren't counted. Hover a failed run to see why. |
| **Modified** | When the notebook file last changed. |

Everything here is read from files on this PC. Nothing is asked of Fabric to draw the list.

## Run several notebooks (Preview)

1. Tick the notebooks to run, or tick the box in the header to select every notebook shown.
2. Press **Run N notebooks**.
3. The confirm lists them by where they run.

**On this PC.** Each notebook runs its saved version from top to bottom, like **Run all**, in the background. Frozen cells are skipped, and a last cell marked to run on Fabric is skipped too, as in a schedule. **At most N at a time** sets how many run together (1 to 4; the app remembers your choice). Each notebook runs in a process of its own, so one that fails doesn't stop the others, and the notebook open in front of you isn't touched. If a notebook has unsaved changes, the confirm says so: the saved version runs. These runs start no Fabric job, but reading or writing OneLake tables still uses capacity.

**On Fabric.** Each notebook is published to its Fabric item (replacing what is there), then run on its workspace's capacity, as you, the same way as its own **Run in Fabric**. The [risk check](finops/risk-check.md) runs for each one first and its findings are shown in the confirm. A notebook the check blocks isn't run. A notebook set to run on Fabric with no workspace yet isn't run either; the confirm names it. **Run only the N on this PC** leaves the Fabric ones out.

While they run, **Last run** shows each notebook as waiting, or running with the cell it is on, and the bell follows the whole run. **Don't start the rest** leaves the notebooks still waiting unstarted; ones already running finish. At the end, a notice says how many succeeded and names any that failed.

Each run is listed on the notebook's **Recent runs** and in **Monitor > Runs** as **Run from Home**, with the notebooks run on Fabric listed as Fabric runs.

## Pinned items (Preview)

Pin what you use most, and it shows in **Pinned** at the top of Home, in the order you pinned it.

- **A notebook:** the pin at the end of its row on Home, or **Pin to Home or unpin** in the command bar while it's open.
- **A Fabric item** (a lakehouse, warehouse, pipeline, semantic model and so on): the pin on its row when you expand a workspace on **Workspaces**.

On Home, click a pinned item's name to open it: a notebook opens here, a Fabric item opens in Fabric. A pinned notebook has **Run**, which opens the same confirm as Run several. Its pin follows it when it's renamed or moved into a folder.

Workspaces keep the pin they already have on **Workspaces**, which puts them at the top of that list.

Pins are kept on this PC, beside the workspace pins and stars. Fabric has no public API for pins, so they don't show in the Fabric portal and don't follow you to another PC. Up to 50 items can be pinned.

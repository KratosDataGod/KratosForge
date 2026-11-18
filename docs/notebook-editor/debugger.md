# Debug a notebook (Preview)

Set breakpoints in notebook cells, run to them, step through the code line by line and look at
the variables at each step. The debugger is in Preview: it shows while **Settings > Preview
features** is on.

## What debugs where

| Where the cell runs | Breakpoints and stepping |
| --- | --- |
| Python on this PC (the notebook's own kernel) | Yes. Breakpoints pause the cell, and you can step, evaluate and stop. |
| Another engine (Databricks Connect, Databricks SQL, Snowflake and the rest in **Engine**) | No. The engine runs the cell to the end. KratosForge says so when you run a cell that has breakpoints, and **Debug cell** says it can't pause there instead of running. |
| In Fabric (**Runs on** is Fabric) | No. Fabric runs the whole notebook as a job. **Run in Fabric** says the breakpoints are ignored. |
| The last cell marked **Run on Fabric (Spark)** | No for that cell, which runs to the end on Fabric Spark. The cells above it still stop at their breakpoints on this PC. |
| Proofs, side by side, Validate, scheduled runs and pipelines | No. They don't run cells from the editor, so they never stop. |

Pausing a remote Spark or Databricks session isn't planned yet. Debugging pipeline runs is an open
question on the roadmap.

## Set breakpoints

- Turn on **Line numbers**, then click a line number, or press **F9** on a line. A red bar marks
  the line. Click it again, or press **F9** again, to clear it.
- Breakpoints follow their lines as you edit: insert lines above and the breakpoint moves down with
  its line; delete the line and the breakpoint goes too.
- Breakpoints are **saved with the notebook**, so they're there after you close and reopen it.
  They're kept in each format KratosForge writes:
  - `.ipynb` (the notebook file, Git, Fabric Git sync and **Export > Jupyter**): in the cell's
    metadata, as `fabric_desktop.breakpoints`, a list of line numbers. Jupyter and Fabric ignore it.
  - **Export > Fabric source (.py)**: in the cell's `# META` block, read back on import.
  - **Export > Databricks source (.py)**: Databricks source has nowhere to keep them, so the export
    lists them under what didn't carry over.

## Run to a breakpoint

- **Run** a cell with breakpoints and it pauses at the first one it reaches. **Run all**, **Run
  cell and below** and **Run above** pause at breakpoints too.
- **Debug cell (pause at the first line)**, on the cell's **...** menu, pauses before the cell's
  first line, with or without breakpoints.
- **Debug from here (this cell and below)**, on the same menu, runs this cell and every cell below
  it under the debugger, starting paused at this cell's first line. Stepping carries from one cell
  to the next: step past a cell's last line and the next cell starts paused at its first line.
  Press **Continue** and the cells after it run to their own breakpoints only.

## While paused

The paused line is highlighted, and a panel under the cell shows:

- **Call stack**: where the cell is, outermost first, such as `(cell) line 5 › double line 3`.
- **Variables** in the paused frame: names, types, sizes and a short value. A variable whose name
  looks like a secret, or whose value came from `getSecret`, shows as `hidden`.
- **Watches** from the Variables pane, evaluated in the paused frame.
- **Evaluate**: type an expression, such as `df.shape` or `n + 1`, to run it in the paused frame.
  It runs as notebook code, so keep it to reads.

| Button | Key | What it does |
| --- | --- | --- |
| Continue | F5 | Runs to the next breakpoint, or to the end of the cell. |
| Step over | F10 | Runs to the next line, without going into calls. |
| Step into | F11 | Goes into a function the cell defines. |
| Step out | Shift+F11 | Runs until the current function returns. |
| Stop | | Cancels the cell, like **Cancel**. Variables the cell set so far are kept, and a Debug from here run stops there. |

After **Stop**, nothing of the debugger is left behind: the next cell runs at full speed with the
variables the stopped cell left.

## Limits

- **Only the running cell's own code stops.** Functions and classes the cell defines pause wherever
  the cell calls them. Library code, functions defined in earlier cells, notebooks run with
  `%run`, and threads the cell starts run at full speed and never pause. To step into a function
  from an earlier cell, put a breakpoint in that cell and debug from there.
- **No time limit while debugging.** Time spent paused counts as running, so the cell time limit is
  off for a cell under the debugger. The memory limit still applies.
- **Evaluate waits up to 10 seconds** for an answer, then shows it when it finishes. **Stop** ends
  an expression that never finishes.
- **Up to 100 breakpoints per cell.**
- **`%%sql` and other magic cells** keep their line numbers: line 1 is the magic line.
- Debugging runs on the cell's own thread with Python's trace hook, so a traced cell runs slower
  than usual. Cells without breakpoints aren't traced at all.

## Check it before it leaves Preview

The automated checks are `sidecar/tests/test_debugger.py`, `sidecar/tests/test_debugger_saved.py`
(saved breakpoints in every format, and a three-cell debug run in both kernels) and
`app/tests/browser/debugger.test.cjs`. Before the feature leaves Preview, also go through this on
Windows, with **Run notebooks in their own process** both on and off:

1. Set breakpoints in two cells, save, close the notebook and reopen it. The red bars are on the
   same lines.
2. Insert two lines above a breakpoint and save. Reopen: the breakpoint is on the moved line.
3. **Debug from here** on the first of three cells. Step over every line: each next cell starts
   paused at line 1. Press **Continue** in the second cell: the third stops only at its breakpoint.
4. In a paused function, **Step into**, **Step out** and **Evaluate** an expression; the variables
   and call stack match the code.
5. **Stop** while paused. The cell shows Cancelled with variables kept, and the next cell runs
   normally.
6. Switch **Engine** to Databricks Connect (or any other engine) and run a cell with breakpoints:
   the message says it can't pause, and the cell runs to the end. **Debug cell** says it can't
   debug there and runs nothing.
7. **Export** as Fabric source and import it again: the breakpoints come back. Export as
   Databricks source: the notes list the breakpoints as not carried over.
8. Open the notebook in Fabric after publishing it: it opens and runs as before.

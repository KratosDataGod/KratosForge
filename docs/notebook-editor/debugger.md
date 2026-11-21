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
  its line. When the whole cell is rewritten at once (**Format cell**, a language change), each
  breakpoint finds its line's code again, wherever it moved.
- A breakpoint is never lost without a word. When its line is deleted, or its code is no longer in
  the cell, it is removed and a message names the line, such as "Removed the breakpoint on line 5,
  because its line was deleted." A breakpoint past the cell's end (its code was changed in another program)
  is removed and named the same way when the cell runs.
- **Editing a cell while it's paused** doesn't change the run: the cell keeps running the code as it
  started, and your edits and breakpoint changes take effect the next time it runs. The paused line
  stays marked on its code wherever your edit moved it, and the panel says so. When that line's code
  is gone, nothing is marked, so the mark is never on a line that isn't the one paused.
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
  looks like a secret, or whose value came from `getSecret`, shows as `hidden`. A frame with more
  than 200 variables lists the first 200 by name and says how many there are. A data frame shows
  its size and first columns, not its rows (evaluate its head(), such as `df.head()`, for those), and a long list or
  text shows its first items, so pausing next to large data stays quick.
- **Stepped over, not into**: after **Step into**, the library functions (such as `json.dumps`) and
  the functions from other cells it didn't go into, by name.
- **Watches** from the Variables pane, evaluated in the paused frame.
- **Evaluate**: type an expression, such as `df.shape` or `n + 1`, to run it in the paused frame.
  It runs as notebook code, so keep it to reads. A breakpoint in a function the expression calls
  doesn't pause the expression, and an expression that calls `exit()` or raises shows the error while the cell
  stays paused. The panel keeps the last 20 answers.

| Button | Key | What it does |
| --- | --- | --- |
| Continue | F5 | Runs to the next breakpoint, or to the end of the cell. |
| Step over | F10 | Runs to the next line, without going into calls. |
| Step into | F11 | Goes into a function the cell defines. |
| Step out | Shift+F11 | Runs until the current function returns. When the caller has no more lines to run, as in `return n * fact(n - 1)`, it pauses as the caller returns. |
| Stop | | Cancels the cell, like **Cancel**. Variables the cell set so far are kept, and a Debug from here run stops there. |

After **Stop**, nothing of the debugger is left behind: the next cell runs at full speed with the
variables the stopped cell left.

## Limits

- **Only the running cell's own code stops.** Functions and classes the cell defines pause wherever
  the cell calls them. Library code, functions defined in earlier cells, notebooks run with
  `%run`, and threads the cell starts run at full speed and never pause. **Step into** on such a
  call steps over it and names it in the panel. To step into a function from an earlier cell, put
  a breakpoint in that cell and debug from there.
- **Callbacks and coroutines pause.** A cell function that library code calls back on the cell's
  own thread (a `sorted` key, a generator `list()` consumes, a coroutine `asyncio.run` runs) is
  the cell's code and stops at its breakpoints; the stack shows only the cell's frames. **Step**
  and **Step out** run through an `await` to the coroutine's next line. Top-level `await` isn't
  supported in cells (use `asyncio.run`), with or without the debugger.
- **Restarting the kernel while paused** ends the paused cell, as **Stop** does, but the variables
  go with the restart. A **Debug from here** run stops there and doesn't go on into the new kernel.
- **If the notebook's own process ends while paused** (with **Run notebooks in their own process**
  on, the process crashed or was ended), the cell ends with an error saying the process stopped,
  nothing stays paused, and the next run starts a fresh process.
- **No time limit while debugging.** Time spent paused counts as running, so the cell time limit is
  off for a cell under the debugger. The memory limit still applies.
- **Evaluate waits up to 10 seconds** for an answer, then shows it when it finishes. **Stop** ends
  an expression that never finishes. A watch that never finishes holds up the pause the same way,
  and **Stop** ends it too. Stop lands between Python steps, so an expression waiting in a single long
  call, such as `time.sleep(30)`, ends when that call returns.
- **Up to 100 breakpoints per cell.**
- **`%%time`, `%%capture` and line magics** keep their line numbers: line 1 is the magic line.
  `%time f()` stops at the cell's breakpoints in `f`. Cell magics that run the cell some
  other way (`%%sql`, `%%html`, `%%timeit`, `%%sh` and the like) run without pausing.
- Debugging runs on the cell's own thread with Python's trace hook, so a traced cell runs slower
  than usual. Cells without breakpoints aren't traced at all.

## Check it before it leaves Preview

The automated checks are `sidecar/tests/test_debugger.py`, `sidecar/tests/test_debugger_saved.py`
(saved breakpoints in every format, and a three-cell debug run in both kernels) and
`sidecar/tests/test_debugger_hardening.py` (stepping past library code, a restart while paused,
large frames and values), `sidecar/tests/test_debugger_robust.py` (malformed debugger and process
messages, a process that dies or a cell that hangs while paused, the stack through errors and
recursion), `sidecar/tests/test_debugger_soak.py` (a thousand pauses and steps, and runs mixing
Stop, Continue and restart, leave no trace hook, thread or memory behind), `sidecar/tests/test_debugger_midstep.py`
(Stop, a restart or the process dying while a step runs), `sidecar/tests/test_debugger_concurrency.py`
(threads, asyncio, library callbacks and magics) and `app/tests/browser/debugger.test.cjs`. Before the feature leaves Preview, also go through this on
Windows, with **Run notebooks in their own process** both on and off:

1. Set breakpoints in two cells, save, close the notebook and reopen it. The red bars are on the
   same lines.
2. Insert two lines above a breakpoint and save. Reopen: the breakpoint is on the moved line.
3. **Debug from here** on the first of three cells. Step over every line: each next cell starts
   paused at line 1. Press **Continue** in the second cell: the third stops only at its breakpoint.
4. In a paused function, **Step into**, **Step out** and **Evaluate** an expression; the variables
   and call stack match the code.
5. **Stop** while paused. The cell shows Cancelled with variables kept, and the next cell runs
   normally. Then pause again and restart the kernel: nothing stays paused, and the run stops.
6. Delete a line that has a breakpoint, and **Format cell** a cell with breakpoints: the first is
   removed with a message naming the line, the others stay on their code.
7. Pause next to a large pandas or Polars DataFrame and a list of millions: the pause is quick, the
   frame shows its size and first columns, and **Step into** a pandas call names it as stepped over.
8. Switch **Engine** to Databricks Connect (or any other engine) and run a cell with breakpoints:
   the message says it can't pause, and the cell runs to the end. **Debug cell** says it can't
   debug there and runs nothing.
9. **Export** as Fabric source and import it again: the breakpoints come back. Export as
   Databricks source: the notes list the breakpoints as not carried over.
10. Open the notebook in Fabric after publishing it: it opens and runs as before.
11. While paused at a breakpoint, insert two lines above it: the paused mark moves with its line and
    the panel says the pause is in the code as it ran. Delete that line: no line is marked.
12. With **Run notebooks in their own process** on, end the notebook's Python process in Task
    Manager while paused: the cell ends with an error, nothing stays paused, and the next cell runs.

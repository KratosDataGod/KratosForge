# Select several cells and run part of a notebook

Work on several cells at once, and run just the cells or the lines you pick, as in Fabric and
Databricks notebooks. Everything here runs on this PC in the notebook's kernel, the same way
**Run cell** does: code that reads or writes a lakehouse still reads or writes it.

## Select several cells

- **Shift+click** a cell (outside its code) to select every cell from the current one to it.
- **Ctrl+click** a cell to add it to the selection or take it out.
- In command mode (press **Esc**), **Shift+Down** and **Shift+Up** (or **Shift+J** and **Shift+K**)
  grow the selection, and **Ctrl+A** selects every cell.
- A plain click, a plain arrow key or **Esc** ends the selection.

While several cells are selected, a strip over the cells shows how many and what you can do to all
of them:

| Action | Key | What happens |
| --- | --- | --- |
| **Run selected** | **Ctrl+Enter** or **Shift+Enter** | Runs the selected code cells in notebook order, stopping at the first failure. Frozen cells are skipped. |
| **Copy** | **C** | Copies the cells; **V** pastes them all below the current cell. **X** cuts them. |
| **Merge** | **Shift+M** | Joins the selected cells into the first one, in order. Cells must all be code or all Markdown, and none frozen. With one cell selected, **Shift+M** merges the cell below. |
| **Freeze** / **Unfreeze** | | Freezes the selected cells, or unfreezes them if all are frozen. |
| **Delete** | **DD** or **Delete** | Deletes the selected cells except frozen ones. **Z** puts them all back where they were. |
| | **Alt+Up** / **Alt+Down** | Moves a block of neighbouring cells up or down. |

**Run > Run selected cells** on the ribbon does the same as the strip's **Run selected**.

When you run more than one cell (Run all, Run all above, Run cell and below, Run selected), the
status bar shows **Last run: N cells in X s**, or how many ran before one failed.

## Run selected text

While you edit a code cell, **Ctrl+Shift+Enter** runs only the text you selected. With nothing
selected it runs the line the cursor is on.

- Lines taken from inside a block (a loop or a function) are dedented so they run on their own.
- In a `%%sql` cell the selection runs as Spark SQL; in a `%%html` cell it shows as HTML.
- The result replaces the cell's output, and the cell says **Ran the selection on this PC**. The cell
  isn't marked as run for [cell dependencies](../notebooks.md#cell-dependencies-and-stale-outputs-preview),
  since the rest of it didn't run.

When **Runs on** is **Fabric**, Fabric runs the whole notebook, so running selected cells or text
isn't offered; switch **Runs on** to **This PC**.

# Find and replace in a notebook

One search across every cell of the notebook, as Fabric and Databricks notebooks have. It runs in
the app and changes only the notebook's cells: nothing is read from or written to OneLake.

## Open it

- **Ctrl+H**, or **Edit > Find and replace**, opens the bar with the replace row showing.
- **Ctrl+F** in command mode (no cell being edited) or **Ctrl+Shift+F** anywhere opens it to find only.
- **Ctrl+F** while you edit a cell still searches just that cell, and the cell's **...** menu has
  **Find in this cell**.

Text you selected in a cell becomes the search.

## Search

| Control | What it does |
| --- | --- |
| **Aa** | Match case |
| **ab** | Match whole words only (`sales` doesn't match `sales_count`) |
| **.\*** | Treat the search as a regular expression |
| **Markdown** | Include Markdown cells (on by default) |
| Up and down arrows, **Enter**, **Shift+Enter** | Go to the next or previous match |

Every match is marked in the cells, and the current one is darker. Going to a match selects its
cell, opens its code if the code was hidden, and opens a Markdown cell for editing.

## Replace

- **Replace** (or **Enter** in the replace box) changes the current match and moves on.
- **Replace all** (or **Ctrl+Enter**) changes every match. The bar then offers **Undo replace all**,
  which puts every changed cell back as it was. It goes away once you edit one of those cells, so it
  never throws away later typing. Each cell's own **Ctrl+Z** also undoes its change.
- With a regular expression, `$1` or `$<name>` in the replacement inserts that group. Lookarounds
  and `^` / `$` see the whole cell, so `(?<=dbo\.)sales` replaces only the `sales` after `dbo.`.
- **Frozen** cells are never changed. Replace all says how many matches it left in frozen cells.

The notebook saves itself after a replace, as it does after typing.

**Esc** closes the bar.

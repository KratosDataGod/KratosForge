# Notebook resources

Each notebook can keep files beside it: lookup CSVs, JSON settings, a small Python module, images. It is KratosForge's version of the **Built-in** resources folder in a Fabric notebook and of workspace files next to a Databricks notebook. Cells read the files as `builtin/<file>`, the same path Fabric uses, so the code runs unchanged in both places.

## Add and manage files

Open a notebook, then the **Resources** tab of the explorer on the left. **Built-in files** lists the notebook's files with their sizes; the **Code snippets** are under them, as before.

- **Add files** opens the file picker. Each file you pick is copied in. When a file of that name is already there, nothing is replaced: you're asked for another name, starting from a free one such as `regions (2).csv`.
- **Open folder** shows the folder in File Explorer, so you can look at the files or drag more in.
- The **⋯** menu on a file has **Copy path** (copies `builtin/<file>` to paste into a cell), **Show in folder**, **Rename** and **Delete**. Delete asks first. Files aren't opened from the app, since one could be a program; use Show in folder.

Limits:

| Limit | Value |
| --- | --- |
| Files per notebook | 100 |
| Size of one file | 50 MB |
| Names | No `/ \ : * ? " < > |`, no `..`, no trailing dot or space, not a name Windows keeps for devices (`CON`, `NUL`, `COM1`...), up to 120 characters |

Fabric's built-in resources have similar per-notebook limits on the number and size of files; check Microsoft's notebook documentation for its current numbers. The limits here are checked when you add a file in the app; files you copy into the folder yourself aren't stopped.

## Use them in a cell

```python
import pandas as pd
regions = pd.read_csv("builtin/regions.csv")

with open("builtin/settings.json") as f:
    settings = json.load(f)

notebookutils.nbResPath          # the notebook's resources folder; its files are in builtin/
```

While a cell runs on this PC, any text in its code that starts with `builtin/` (or `./builtin/`) means the notebook's `builtin` folder. That works for `open()`, pandas, polars, DuckDB, `notebookutils.fs` and anything else that takes a path, and for text built from it, such as `f"builtin/{name}"` or `"builtin/" + name`. What is left as written: the bare word `"builtin"` (it is data, not a path), text after a value in an f-string (`f"{base}builtin/x.csv"` may be a OneLake path), and every string when the notebook has no resources folder yet. A `..` that would climb out of `builtin` is dropped, so the path stays inside the folder. Two cases need `notebookutils.nbResPath` instead, as they would in any folder: a path built without the slash (`os.path.join("builtin", name)`) and paths inside a `%%sql` cell.

To import a module you added, put the folder on the path first: `sys.path.append(f"{notebookutils.nbResPath}/builtin")`.

**Why the path is rewritten rather than the working folder changed.** Fabric starts each notebook with its resources folder as the working folder. Here, cells usually run inside the engine, which runs every notebook and the app's own work, so changing its working folder would move every relative path for all of them. Instead the kernel rewrites `builtin/...` text in the cell's code as the cell starts. The working folder stays as it was, and only text that starts with `builtin/` changes; print one and you see the full path. The same happens when cells run in a process of their own (Settings), in Validate, Prove and Side by side, in schedules on this PC, and in notebook runs started by an external AI tool.

## Where the files are

```
<notebooks folder>/Sales.ipynb
<notebooks folder>/Sales.resources/builtin/regions.csv
```

The folder sits beside the notebook, in the app's notebooks folder or in a notebooks repository's `notebooks/` folder, so it goes into Git with the notebook. It is made when you add the first file (or select Open folder). Moving the notebook to another repository in Source control moves the folder too: both are copied and checked before the originals are removed, and nothing already at the destination is replaced. Renaming the notebook renames the folder; when a folder of the new name is already there, nothing is renamed. Deleting a notebook (it goes to Recently deleted, see [Notebook files](notebook-files.md)) never deletes its resources folder: the folder stays on this PC where it was, and the notebook restored under its own name, or a new notebook with the same name, picks it up again. Delete the folder in File Explorer when you no longer need the files; the delete's result names it (`resources_kept`).

## How this works

- **What it reads:** the files in the notebook's resources folder on this PC.
- **What it changes:** Add files writes each file into `<notebook>.resources/builtin/`, reserving the name first so an existing file is never overwritten. Rename renames one file there, never over another. Delete removes one file. Open folder creates the folder when it isn't there yet. Renaming or moving the notebook renames or moves the folder with it. Deleting the notebook leaves the folder on this PC. Cells that write to `builtin/...` (for example `df.to_csv("builtin/out.csv")`) write into the folder, as in Fabric. Nothing in Fabric or OneLake changes.
- **Where results go:** the files stay on this PC. **Publishing a notebook to Fabric doesn't upload its resources yet**, so a published notebook that reads `builtin/...` needs those files added to its built-in resources in Fabric by hand. Run in Fabric and schedules use the published notebook, so the same applies there.

The engine calls are `notebook_resources` (read only), `notebook_resource_add`, `notebook_resource_rename`, `notebook_resource_delete` and `notebook_resource_reveal`; every one but the first is recorded in the [audit trail](user-guide.md#audit-trail), with file names but never file contents.

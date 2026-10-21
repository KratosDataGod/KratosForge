# Notebook files

Find, open, copy and delete notebooks, and edit a Fabric notebook on this PC. Everything here works on notebook files on this PC: a notebook is never replaced by another one, and a file you open from elsewhere is never changed or moved.

## All notebooks

On **Home**, under **Recent notebooks**, select **All notebooks**. The page lists every notebook in the app's own folder and in your notebooks repositories (Source control), with where it's saved, the Fabric workspace it was last published to, and when it changed. When you have a repository, the **Saved in** chips filter the list.

Each row's **⋯** menu has **Open**, **Save a copy**, **Rename**, **Move to folder**, **Move to another place** and **Delete**. Notebooks can be grouped into folders: see [Notebook folders](notebook-folders.md). The command bar (Ctrl+K) finds **All notebooks**, **Search inside notebooks**, **Open a notebook file** and **Save a copy of this notebook**.

## Search inside notebooks

Type in the search box on All notebooks. It finds notebook names and text in cells, code and Markdown, ignoring case, and shows each matching line with its cell. **Go to cell** opens the notebook at that cell. **Names only** skips cell text.

Search reads the saved notebook files on this PC. The open notebook's unsaved edits are searched too. Outputs aren't searched, and neither are notebooks in Fabric: copy one here first with [Edit on this PC](#edit-a-fabric-notebook-on-this-pc-preview).

## Open a notebook file

**Open a notebook file…** on All notebooks picks an `.ipynb` file anywhere on this PC, such as one from Jupyter, VS Code or a download from Fabric. KratosForge adds a copy to your notebooks and opens it; the file you picked isn't changed or moved. Before it adds the copy, it says:

- how many cells it has; outputs saved in the file are left out, so run it to see them here;
- the default lakehouse Fabric saved in it, which is attached. Running it here reads that lakehouse's tables from OneLake, which uses capacity;
- cells that use Spark. On this PC `spark` runs on DuckDB's Spark-compatible API, so check those cells with [Validate](validate-notebook.md).

When you already have a notebook with the file's name, a free name is suggested. Files over 20 MB, and files that aren't Jupyter notebooks (format 4), aren't opened.

## Save a copy

**Save a copy** on the notebook's **Home** tab, or on All notebooks, saves the notebook under a new name, in the place you pick. The copy has the same cells, lakehouse, parameters and publish workspace; the original stays as it is. The copy isn't published: its first **Publish** makes a new Fabric item under the copy's name. The original keeps its Fabric item, its schedules and its Prove history.

## Delete and restore

**Delete** asks first, and says what else it touches:

- The notebook goes to **Recently deleted** on All notebooks, kept on this PC for 30 days, then removed for good. **Restore** puts it back where it was (in the app's own folder when its repository isn't open any more). If another notebook has taken its name, Restore asks for a new one.
- Its schedules on this PC are **turned off**, not deleted, so none starts a notebook that isn't there. Restore turns them back on. A notebook restored under a new name leaves them off, since a schedule runs a notebook by name.
- Its Fabric item isn't deleted. Delete it in Fabric if you want it gone too.
- Its [resources folder](notebook-resources.md) stays where it is on this PC. Restoring the notebook under its own name, or making a new notebook with that name, picks it up again; a notebook restored under a new name starts without it.
- In a notebooks repository, the file leaves the repository's folder, which shows as a change to commit there.

Recently deleted is in the app's data folder, outside every repository, so a delete never adds files to Git.

## Edit a Fabric notebook on this PC (Preview)

In **Workspaces**, select a notebook item to preview it, then select **Edit on this PC**. KratosForge reads the notebook's cells from Fabric (its definition) and saves them as a notebook here. Nothing in Fabric changes, and no Fabric job runs.

- The notebook keeps the item's name and is set to publish to its workspace, so **Publish** updates the same Fabric item instead of making a new one. If you already have a notebook with that name that isn't this item, rename it first.
- Its default lakehouse is attached.
- The setup cell KratosForge adds when it publishes is left out; Publish adds it again.
- If the notebook is already on this PC, Edit on this PC opens that one instead of copying it twice.

## When Publish finds changes made in Fabric (Preview)

Each time KratosForge publishes a notebook, it keeps the cells it sent on this PC. The next **Publish**, **Run in Fabric** or new Fabric schedule (each publishes first) first reads the item's cells in Fabric. When someone changed them there since, nothing is sent, and a dialog shows what changed in Fabric and what changed here:

- **Copy Fabric's version here first** saves Fabric's cells as a separate notebook, *name* (Fabric), not linked to the item, so you can compare them and bring changes across. Nothing is published.
- **Replace with mine** asks once more, then publishes your notebook over the changes made in Fabric. Those changes are lost unless you kept a copy.
- **Cancel** publishes nothing.

Publish writes the Fabric item that has the notebook's name. When that's no longer the item it was published to (someone renamed that one in Fabric and made another under this name), the dialog says so and nothing is sent until you choose. If the check itself can't reach Fabric, nothing is published and the error says so; try again. A notebook published before this check existed is checked from its next publish on. When the item was deleted in Fabric, Publish makes it again.

## What it reads and writes

| Action | Reads | Writes |
| --- | --- | --- |
| All notebooks, search, Recently deleted | Notebook files on this PC | Nothing |
| Open a notebook file | The file you pick | A new notebook in the place you pick |
| Save a copy | The notebook | A new notebook in the place you pick |
| Delete | The notebook | Moves it to Recently deleted; turns off its schedules on this PC |
| Restore | Recently deleted | Puts the notebook back; turns its schedules on |
| Edit on this PC | The item's definition in Fabric | A new notebook on this PC |
| Publish check | The item's definition in Fabric; the cells last published, on this PC | Nothing (Publish then writes the item, as before) |

Reading an item's definition uses the Fabric permission KratosForge already asks for to publish notebooks; see [Permissions](permissions.md).

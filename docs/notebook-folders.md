# Notebook folders

Group your notebooks into folders, as you can with workspace folders in Fabric and Databricks. A folder is part of a notebook's name: **Sales/Daily load** is the notebook **Daily load** in the **Sales** folder. On disk it is `Sales/Daily load.ipynb` in the notebooks folder of the place it's saved in (the app's own folder, or a notebooks repository's `notebooks` folder). Its resources are in `Sales/Daily load.resources/` beside it.

Nothing here replaces or deletes a notebook, and nothing in Fabric changes.

## See notebooks by folder

On **All notebooks** (Home, under Recent notebooks), notebooks are grouped by folder once you have one. Each folder shows how many notebooks it holds, counting its subfolders, and folds with its arrow. Notebooks in no folder are under **Not in a folder**. When you have notebooks repositories, a folder shows which place it's in, and the **Saved in** chips filter folders too.

In a notebook, the explorer's **Notebooks** tab shows the same folders, so you can open another notebook without leaving it.

## New folder

**New folder** on All notebooks makes an empty folder. When you have notebooks repositories, pick the place to make it in. Type `Sales/Archive` to make a folder inside another, or use **New folder inside…** on a folder's **⋯** menu.

- Folders go 3 levels deep. A notebook's name can be `Sales/Archive/2026/Q1 backfill`, no deeper.
- Each folder name follows the notebook name rule: no `< > : " \ | ? *`, and no dot at the end. A folder name can't start with a dot, and can't end with `.resources`, which is where a notebook keeps its resources. Names Windows keeps for devices (CON, PRN, AUX, NUL, COM1 to COM9, LPT1 to LPT9, with or without an extension, in any case) can't be a folder or notebook name.

**New notebook here**, on a folder's **⋯** menu, makes a new notebook in that folder (Sales/Notebook 1).

## Move a notebook to a folder

On All notebooks, a notebook's **⋯** menu has **Move to folder…**. The command bar (Ctrl+K) also finds **Move this notebook to a folder** for the open notebook. Pick a folder in the notebook's place, or **Not in a folder**. Moving it is a rename, so what goes with a renamed notebook goes with this one:

- its resources folder, which moves beside it;
- its versions, comments and session;
- this PC's schedules that run it, which run it by its new name;
- its environment and its link to its Fabric item.

If the folder already has a notebook with that name, nothing moves. To move a notebook to another place (a notebooks repository), use **Move to another place…**; it keeps its folders there.

## Rename a folder

**Rename folder…** on a folder's **⋯** menu renames it where it is. The folder is renamed on disk with everything in it, so each notebook in it gets its new name (Sales/Daily load becomes Sales EMEA/Daily load), and its versions, this PC's schedules, session, environment and Fabric link follow it. If a folder or notebook with the new name is already there, nothing is renamed.

## Delete a folder

**Delete folder…** deletes a folder only when it's empty. A folder with notebooks in it can't be deleted: move or delete them first. A folder with anything else in it (another folder, a file, or the resources folder of a deleted notebook) can't be deleted either, and the message says what's in it. This way no notebook is ever deleted with a folder. Deleting an empty folder asks once.

## Recently deleted

A notebook deleted from a folder keeps its whole name in **Recently deleted**. **Restore** puts it back in that folder, making the folder again if it was deleted meanwhile.

## Publishing a notebook from a folder (Preview)

Fabric names its notebook item after the notebook's own name, without its folders: **Sales/Daily load** publishes as **Daily load**. KratosForge doesn't make or use Fabric workspace folders, so the item goes where Publish has always put it. Moving a notebook into a folder or renaming its folder keeps its Fabric item, because the item's name doesn't change.

Two notebooks in different folders can have the same name, but they can't both publish to one workspace: Publish stops, nothing is sent, and the message names the notebook already linked to that item. Rename one of them, or publish it to another workspace.

This hasn't been tried on a live tenant yet, so it's marked Preview.

## How this works

Every write, in one place. In a notebooks repository each shows as a change to commit.

| Action | What it writes | Refuses when |
| --- | --- | --- |
| New folder | An empty folder in the place's notebooks folder | It's already there, has a bad name, ends in `.resources` or is more than 3 levels deep |
| New notebook here | `<folder>/Notebook 1.ipynb` | Never: it takes the next free number |
| Move to folder | Renames the notebook's file and its resources folder, moves its versions (in the app's data folder) and points this PC's schedules, its session, environment and Fabric link at the new name | The folder already has a notebook with that name, or the folder isn't there |
| Rename folder | Renames the folder on disk, then moves each notebook's versions, schedules, session, environment and Fabric link to its new name | A folder or notebook with the new name is already there |
| Delete folder | Removes the empty folder | Anything is in it |
| Publish | The Fabric item, named without the folders | Another notebook here already publishes to that item in that workspace |

If something kept by a notebook's name can't follow it (its session, environment, Fabric link, versions or this PC's schedules), the notebooks still move, every other step still runs, and a warning names what didn't follow and what to do about it.

## Known limitations

- The check before publishing that two notebooks would share one Fabric item only sees notebooks linked to an item from this PC. A notebook published from another PC, or an item made in Fabric, with the same name is replaced by Publish as before.
- Renaming a folder changes only its last part; a folder can't move to another parent folder.
- A notebook in Recently deleted keeps the folder it was deleted from, so restoring it after that folder was renamed makes the old folder again.
- Fabric workspace folders aren't used.

Fabric schedules belong to the Fabric item, so they don't change when a notebook moves. A notebook in a folder isn't searched any differently: search on All notebooks finds it by its whole name.

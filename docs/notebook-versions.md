# Notebook version history

Every notebook keeps its earlier versions on this PC, so you can see what changed and go back. This is like version history in Fabric notebooks and revision history in Databricks notebooks.

## Versions and checkpoints

On the notebook's **Home** tab, select **Versions**. The list shows the notebook's versions, newest first, grouped by day. **Same as now** marks the versions whose cells match the notebook as it is.

- **Saved:** each save keeps a version. Autosave saves a few seconds after you stop typing, so the saves within 5 minutes of each other share one version, the newest. A save that changes nothing keeps nothing. Saved versions are kept for 30 days, up to 100 per notebook.
- **Checkpoint:** **Create checkpoint…** keeps the notebook as it is now under a name you give it, such as "Before the refactor". Checkpoints are kept until you delete them.
- **Before a restore:** what the notebook was just before you restored another version. Restore keeps it automatically.
- **Changed outside the app:** the notebook as another program, such as VS Code or a Git pull, saved it, kept when KratosForge found the change before saving.
- **Not saved (changed outside):** your edits from that moment, which weren't saved over the other program's change. See [Edit the same notebook in VS Code or another program](notebook-files.md#edit-the-same-notebook-in-vs-code-or-another-program).

The command bar (Ctrl+K) also finds **Version history** and **Create a checkpoint**.

## Compare

**Compare** on a version shows how the notebook now (including edits not saved yet) differs from it, cell by cell: cells changed, added, removed or reordered, with the lines that changed. Only cell code and Markdown are compared, not outputs. **Compare with last commit** does the same against the notebook's last Git commit when it's in a notebooks repository.

## Restore

**Restore** puts that version's cells back. Before it does, the notebook as it is now is kept as a **Before a restore** version, so you can restore that one to undo it. The notebook's lakehouse, where it runs and its publish workspace stay as they are now. Restore doesn't publish: the Fabric item changes only when you publish the notebook.

## What it reads and writes

| Action | Reads | Writes |
| --- | --- | --- |
| Save (yours, or autosave) | The notebook | A version on this PC (or replaces the newest saved version within 5 minutes) |
| Versions, Compare | Versions on this PC; the last Git commit for Compare with last commit | Nothing |
| Create checkpoint | The notebook | A checkpoint on this PC |
| Restore | The version | The notebook's cells, and a Before a restore version first |
| Delete a version | | Removes that version from this PC |

Versions are kept in the app's data folder (`notebook-versions`), outside every notebooks repository. They're never added to Git, published to Fabric or written to OneLake. They follow a notebook you rename. A new notebook given the name of a deleted one sees the deleted one's versions. Each version is one file. An `index.json` beside them lists every version without its cells, so saving and opening Versions stay quick however long the history grows; the app rebuilds it from the version files if it is damaged or out of step.

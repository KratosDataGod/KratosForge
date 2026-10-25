# VS Code and KratosForge (Preview)

Use VS Code and KratosForge together on the same notebooks. Notebooks are plain `.ipynb` files, so VS Code opens them as they are, and KratosForge keeps running them on this PC, side by side with Fabric, and publishing them.

| You want to | Where |
| --- | --- |
| Open a notebook, or its folder, in VS Code | The notebook's **Home** tab: **Open in VS Code** |
| Let Copilot in VS Code use KratosForge | **Settings › Integrations › Add a tool**, pick VS Code, then **Add to VS Code** ([Connect your AI tool](ai/README.md)) |
| Keep edits from both editors | Nothing to do: see [Editing in both](#editing-in-both) |

## Open in VS Code

On a notebook's **Home** tab, **Open in VS Code** saves the notebook and opens its file in VS Code. The arrow beside it has:

- **Open the repository folder in VS Code**, for a notebook in a notebooks repository (Source control), or **Open the notebooks folder in VS Code** for the app's own folder;
- **Open this notebook in VS Code Insiders**.

VS Code has to be installed. KratosForge asks Windows to open a `vscode://file/…` link, which VS Code handles; only an existing notebook file or folder is opened this way.

## Editing in both

When you come back to KratosForge and the open notebook has no unsaved edits here, it shows the file as VS Code saved it. Cells whose code didn't change keep their outputs, because the session that made them is still running.

When both sides have changes, KratosForge doesn't save over the other one: it asks **Keep mine** or **Load its version**, and keeps both under **Versions**. See [Edit the same notebook in VS Code or another program](notebook-files.md#edit-the-same-notebook-in-vs-code-or-another-program).

## What runs where

Opening a notebook in VS Code doesn't run anything. Cells you run in VS Code use whatever kernel you pick there; to run on this PC with KratosForge's engine, lakehouse and session, run them in KratosForge for now. A KratosForge kernel for VS Code is planned.

## What it reads and writes

| Action | Reads | Writes |
| --- | --- | --- |
| Open in VS Code | Where the notebook's file is | The notebook, saved first (as **Save**) |
| Coming back into focus | The notebook's file, to see if its cells changed | Nothing |

# KratosForge for VS Code

Run notebooks on this PC in [KratosForge](https://github.com/KratosDataGod/KratosForge) from VS Code: pick **KratosForge · This PC** in a notebook's kernel picker, and its cells run in that notebook's own session in KratosForge, with the notebook's default lakehouse, the same variables and limits as running them in the app. Output streams into VS Code as the cell runs, and Stop stops it.

Preview. Windows, with KratosForge installed and open.

## Use it

1. In KratosForge, **Settings > Integrations > VS Code**: turn on **Let VS Code run notebooks**.
2. Open a notebook from KratosForge's notebooks folder, or from a notebooks repository KratosForge has open in Source control (**Open in VS Code** on a notebook does this).
3. Pick **KratosForge · This PC** as the kernel and run a cell. The first time, VS Code shows a pairing code and KratosForge asks whether to pair this window with the same code. Check they match and choose **Pair this window**. The pairing covers the workspace folder holding the notebook (or the notebook's own folder when none is open); a notebook in another folder pairs that folder separately.

Writing a table or file to OneLake from a cell asks in KratosForge every time. Publish, Run in Fabric and Side by side are done in KratosForge.

Commands: **KratosForge: Pair this window with KratosForge**, **KratosForge: End this window's pairing** (also from the status bar item), **KratosForge: Open in KratosForge**.

How it works, what it can and can't do, and for IT: [docs/vscode.md](https://github.com/KratosDataGod/KratosForge/blob/main/docs/vscode.md#run-cells-from-vs-code).

## Develop

```
npm ci
npm test          # compiles, then runs test/*.test.cjs against the real engine (FABRIC_DESKTOP_PYTHON)
```

`src/channel.ts` is the editor channel client and `src/outputs.ts` maps KratosForge's outputs to VS Code's; neither uses the VS Code API, so both are tested without VS Code. `src/extension.ts` is the notebook controller.

Licensed as KratosForge is (see the repository).

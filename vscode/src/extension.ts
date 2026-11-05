// KratosForge for VS Code: "KratosForge · This PC" in the notebook kernel picker. Cells run in the
// notebook's own session in KratosForge on this PC, over the editor channel (channel.ts), after
// the person pairs this window in the app. Writing to OneLake from a cell still asks in KratosForge.

import * as path from "path";
import * as vscode from "vscode";
import { Channel, ChannelError, Snapshot } from "./channel";
import { convert, failed, Output } from "./outputs";

const SECRET = "kratosforge.pairing";

// A pairing is bound to the folder it paired from, so each folder has its own channel and token.
const channels = new Map<string, Channel>();
const pairings = new Map<string, Promise<boolean>>();
let status: vscode.StatusBarItem;

/** The folder a notebook pairs from: its workspace folder, else the folder holding the file. */
function folderFor(notebook: vscode.NotebookDocument | undefined): string {
  if (!notebook || notebook.uri.scheme !== "file") return "";
  return vscode.workspace.getWorkspaceFolder(notebook.uri)?.uri.fsPath ?? path.dirname(notebook.uri.fsPath);
}

function channelFor(folder: string): Channel {
  let ch = channels.get(folder);
  if (!ch) channels.set(folder, ch = new Channel());
  return ch;
}

function toOutput(out: Output): vscode.NotebookCellOutput {
  if ("error" in out) return new vscode.NotebookCellOutput([vscode.NotebookCellOutputItem.error(out.error)]);
  return new vscode.NotebookCellOutput(out.items.map((i) =>
    i.bytes ? new vscode.NotebookCellOutputItem(i.bytes, i.mime) : vscode.NotebookCellOutputItem.text(i.text ?? "", i.mime)));
}

function showStatus(paired: boolean): void {
  status.text = paired ? "$(plug) KratosForge" : "$(debug-disconnect) KratosForge";
  status.tooltip = paired ? "This window is paired with KratosForge: cells run on this PC in the notebook's session."
    : "Not paired with KratosForge. Run a cell with the KratosForge kernel to pair.";
  status.show();
}

async function ensurePaired(context: vscode.ExtensionContext, folder: string): Promise<boolean> {
  const channel = channelFor(folder);
  const key = `${SECRET}:${folder}`;
  if (!channel.token) channel.token = (await context.secrets.get(key)) || "";
  if (channel.token) {
    try {
      await channel.status();
      showStatus(true);
      return true;
    } catch (err) {
      if (!(err instanceof ChannelError) || err.code !== "not_paired") throw err;
      channel.token = "";
      await context.secrets.delete(key);
    }
  }
  let asking = pairings.get(folder);
  if (!asking) {
    asking = pair(context, folder, channel).finally(() => { pairings.delete(folder); });
    pairings.set(folder, asking);
  }
  return asking;
}

async function pair(context: vscode.ExtensionContext, folder: string, channel: Channel): Promise<boolean> {
  const client = `KratosForge for VS Code ${context.extension.packageJSON.version}`;
  return vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "KratosForge", cancellable: true },
    async (progress, cancel) => {
      progress.report({ message: `Asking KratosForge to pair ${folder}…` });
      const got = await channel.pair(folder, client, (code) => progress.report({
        message: `Pairing code ${code}. Check KratosForge shows the same code, then choose Pair this window there.` }),
      () => cancel.isCancellationRequested);
      if (got.keep !== "session") await context.secrets.store(`${SECRET}:${folder}`, got.token);
      showStatus(true);
      vscode.window.showInformationMessage("Paired with KratosForge. Cells run on this PC in each notebook's session.");
      return true;
    });
}

async function execute(context: vscode.ExtensionContext, controller: vscode.NotebookController,
  cells: vscode.NotebookCell[], notebook: vscode.NotebookDocument): Promise<void> {
  if (notebook.uri.scheme !== "file" || notebook.isUntitled) {
    vscode.window.showWarningMessage("Save the notebook in KratosForge's notebooks folder or a notebooks repository first.");
    return;
  }
  const folder = folderFor(notebook);
  const channel = channelFor(folder);
  for (const cell of cells) {
    if (cell.kind !== vscode.NotebookCellKind.Code) continue;
    const run = controller.createNotebookCellExecution(cell);
    run.start(Date.now());
    run.clearOutput();
    let stopped = false;
    run.token.onCancellationRequested(() => {
      stopped = true;
      channel.interrupt(notebook.uri.fsPath).catch(() => undefined);
    });
    try {
      await ensurePaired(context, folder);
      const show = (snap: Snapshot) => {
        if (snap.execution_count) run.executionOrder = snap.execution_count;
        if (Array.isArray(snap.outputs)) run.replaceOutput(snap.outputs.map((o) => toOutput(convert(o))));
      };
      const last = await channel.run(notebook.uri.fsPath, cell.document.getText(), show);
      run.end(!failed(last.status, last.outputs ?? []) && last.status !== "cancelled", Date.now());
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      run.replaceOutput(new vscode.NotebookCellOutput([vscode.NotebookCellOutputItem.error({ name: "KratosForge", message })]));
      run.end(false, Date.now());
      if (err instanceof ChannelError && err.code === "not_paired") showStatus(false);
      if (err instanceof ChannelError && err.code === "not_listening") {
        const open = "Open KratosForge";
        vscode.window.showWarningMessage(message, open).then((pick) => {
          if (pick === open) vscode.env.openExternal(vscode.Uri.parse("kratosforge://page/settings"));
        });
      }
      return; // the cells after it don't run, as Run all stops at an error
    }
    if (stopped) return;
  }
}

export function activate(context: vscode.ExtensionContext): void {
  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.command = "kratosforge.unpair";
  showStatus(false);
  const controller = vscode.notebooks.createNotebookController("kratosforge-this-pc", "jupyter-notebook", "KratosForge · This PC");
  controller.description = "Runs in the notebook's session in KratosForge";
  controller.detail = "On this PC, with the notebook's lakehouse. Writes to OneLake ask in KratosForge.";
  controller.supportedLanguages = ["python"];
  controller.supportsExecutionOrder = true;
  controller.executeHandler = (cells, notebook, ctl) => execute(context, ctl, cells, notebook);
  controller.interruptHandler = async (notebook) => {
    await channelFor(folderFor(notebook)).interrupt(notebook.uri.fsPath).catch(() => undefined);
  };
  context.subscriptions.push(controller, status,
    vscode.commands.registerCommand("kratosforge.pair", () => {
      const folder = folderFor(vscode.window.activeNotebookEditor?.notebook);
      if (!folder) return vscode.window.showInformationMessage("Open a saved notebook to pair its folder with KratosForge.");
      return ensurePaired(context, folder).catch((err) =>
        vscode.window.showWarningMessage(err instanceof Error ? err.message : String(err)));
    }),
    vscode.commands.registerCommand("kratosforge.unpair", async () => {
      const folder = folderFor(vscode.window.activeNotebookEditor?.notebook);
      const channel = channelFor(folder);
      if (folder && !channel.token) channel.token = (await context.secrets.get(`${SECRET}:${folder}`)) || "";
      if (!channel.token) {
        vscode.window.showInformationMessage("This window isn't paired with KratosForge.");
        return;
      }
      const end = "End pairing";
      if (await vscode.window.showInformationMessage("This window is paired with KratosForge.", end) !== end) return;
      await channel.unpair().catch(() => undefined);
      channel.token = "";
      await context.secrets.delete(`${SECRET}:${folder}`);
      showStatus(false);
    }),
    vscode.commands.registerCommand("kratosforge.openInApp", () => {
      const nb = vscode.window.activeNotebookEditor?.notebook;
      const link = nb && nb.uri.scheme === "file" ? `kratosforge://notebook?path=${encodeURIComponent(nb.uri.fsPath)}` : "kratosforge://";
      vscode.env.openExternal(vscode.Uri.parse(link));
    }));
}

export function deactivate(): void {
  // A session pairing ends with KratosForge, and a kept one stays for the next window.
}

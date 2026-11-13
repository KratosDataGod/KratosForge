// kratosforge:// links the extension opens (docs/vscode.md, "Open in KratosForge"). No VS Code API
// here, so the tests can check them.

/** The link that shows the notebook saved at `fsPath` in KratosForge: every character of the path
 * %-encoded, so spaces, `&`, `+`, `#`, `%` and letters outside ASCII reach the app as they are. */
export function notebookLink(fsPath: string): string {
  return `kratosforge://notebook?path=${encodeURIComponent(fsPath)}`;
}

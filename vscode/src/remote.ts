// Windows the extension can't serve (docs/vscode.md, "Paths"). KratosForge runs cells on this PC,
// in the notebook's session in the app, so only a local window, with the notebook saved on this PC, can use
// it. In a Remote-SSH, WSL, Dev Container, Codespaces or tunnel window the extension says so once and never
// contacts KratosForge. No VS Code API here, so the tests can check it.

const KINDS: Record<string, string> = {
  "ssh-remote": "Remote-SSH",
  wsl: "WSL",
  "dev-container": "Dev Container",
  "attached-container": "Dev Container",
  codespaces: "Codespaces",
  tunnel: "Remote Tunnel",
};

/** What kind of remote window this is, as people call it, or undefined for a local window. */
export function remoteKind(remoteName: string | undefined): string | undefined {
  if (!remoteName) return undefined;
  return KINDS[remoteName.toLowerCase()] ?? "remote";
}

/** What the window says, once, when asked to run, pair or open in KratosForge. */
export function remoteMessage(kind: string): string {
  return `KratosForge runs cells only in a local VS Code window, with the notebook saved on this PC. This is a `
    + `${kind} window, so it doesn't contact KratosForge. Open the notebook in a local window, or in KratosForge.`;
}

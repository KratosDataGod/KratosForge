// Packages kratosforge.vsix so that two builds of one commit come out byte for byte the same
// (docs/vscode.md, "Rebuilding it yourself"). Run after tsc, by `npm run package`.
// - Every file the .vsix can take gets mode 0644, whatever the checkout's umask: vsce stores each
//   file's mode in the zip.
// - vsce runs with TZ=UTC: zip entries hold a local date and time, so the same SOURCE_DATE_EPOCH
//   would otherwise be stored differently in another time zone.
// - With SOURCE_DATE_EPOCH set, vsce stores that time for every file and sorts the entries.
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const root = path.join(__dirname, "..");
const skip = new Set(["node_modules", ".git"]);

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (skip.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (entry.isFile()) fs.chmodSync(full, 0o644);
  }
}

walk(root);
const vsce = require.resolve("@vscode/vsce/vsce");
const run = spawnSync(process.execPath, [vsce, "package", "--no-dependencies", "--skip-license", "-o", "kratosforge.vsix"],
  { cwd: root, stdio: "inherit", env: { ...process.env, TZ: "UTC" } });
process.exit(run.status ?? 1);

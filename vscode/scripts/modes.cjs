// Before packaging: every file the .vsix can take gets mode 0644, whatever the umask of the checkout.
// vsce stores each file's mode in the zip, so without this the same commit builds a different file on
// a machine with another umask. With SOURCE_DATE_EPOCH set (vsce then stores that time and sorts the
// entries), two builds of one commit come out byte for byte the same (docs/vscode.md).
const fs = require("node:fs");
const path = require("node:path");

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

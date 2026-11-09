// The kratosforge:// link "Open in KratosForge" opens (src/links.ts). deep_link.rs's tests read the same
// links back, in this form and as VS Code writes them out again.
const { test } = require("node:test");
const assert = require("node:assert");
const { notebookLink } = require("../out/links.js");

test("every character of a real-world path is encoded, so the app reads back the same file", () => {
  for (const file of [
    "C:\\Users\\Ana Lima\\Documents\\Daily sales.ipynb",
    "C:\\Users\\José\\📊 Q1\\a%20b.ipynb",
    "C:\\a+b\\c&d=e.ipynb",
    "C:\\C# samples\\x.ipynb",
    "C:\\Users\\me\\OneDrive - Contoso\\Documents\\x.ipynb",
    "Z:\\team\\x.ipynb",
    `C:\\${"d".repeat(300)}\\x.ipynb`,
  ]) {
    const link = notebookLink(file);
    const query = link.slice("kratosforge://notebook?path=".length);
    assert.match(query, /^[A-Za-z0-9%\-_.!~*'()]*$/, file);
    assert.strictEqual(decodeURIComponent(query), file);
  }
});

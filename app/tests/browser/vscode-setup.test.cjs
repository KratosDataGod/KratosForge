// Set up a repository for VS Code, against the real engine: the dialog lists the files it would
// add, leaves a file that's already there alone (showing Copilot's text to paste), and adds the
// rest as changes to commit.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

let app, root;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1280, height: 900 }, engine: ["git_create", "git_vscode_plan", "git_vscode_apply", "git_status"] });
  root = path.join(app.engineHome, "sales-notebooks");
  root = await page().evaluate((p) => rpc("git_create", { path: p, kind: "notebooks" }).then((st) => st.root || p), root);
  fs.mkdirSync(path.join(root, ".github"), { recursive: true });
  fs.writeFileSync(path.join(root, ".github", "copilot-instructions.md"), "Ours.\n");
});

after(async () => { if (app) await app.close(); });

test("Set up for VS Code adds the new files and leaves the rest as they are", { skip }, async () => {
  await page().evaluate((r) => { window.__after = null; vscodeSetupDialog(r, "sales-notebooks", (st) => { window.__after = st; }); }, root);
  const dialog = page().getByRole("dialog");
  await dialog.getByText("Set up sales-notebooks for VS Code").waitFor();
  assert.strictEqual(await dialog.getByRole("checkbox", { name: ".github/copilot-instructions.md" }).isDisabled(), true);
  await dialog.getByText("Show the text to add").click();
  assert.match(await dialog.locator(".vscode-setup-text").textContent(), /## KratosForge/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "vscode-setup.png") });
  await dialog.getByRole("checkbox", { name: ".vscode/extensions.json" }).uncheck();
  await dialog.getByRole("button", { name: "Add 2 files" }).click();
  await page().waitForFunction(() => window.__after);
  assert.ok(fs.existsSync(path.join(root, ".vscode", "tasks.json")));
  assert.ok(fs.existsSync(path.join(root, ".vscode", "mcp.json")));
  assert.ok(!fs.existsSync(path.join(root, ".vscode", "extensions.json")), "unticked, so not added");
  assert.strictEqual(fs.readFileSync(path.join(root, ".github", "copilot-instructions.md"), "utf8"), "Ours.\n");
  assert.deepStrictEqual(app.errors, []);
});

test("A notebook in a repository offers the setup from its VS Code menu", { skip }, async () => {
  const items = await page().evaluate((r) => vscodeMenu({ name: "Daily", place: { root: r, label: "sales-notebooks" } }).map((i) => i.label || i.info), root);
  assert.ok(items.includes("Set up this repository for VS Code…"));
  const home = await page().evaluate(() => vscodeMenu({ name: "Daily", place: null }).map((i) => i.label || i.info));
  assert.ok(!home.includes("Set up this repository for VS Code…"));
});

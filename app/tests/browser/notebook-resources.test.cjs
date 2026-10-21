// Notebook resources (L4): the explorer's Resources tab against the real engine. Files are added
// through the file input, a name already there is never replaced, Rename and Delete change one
// file, and a cell reads the file as builtin/<file>. Nothing reaches Fabric or OneLake.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const CSV = Buffer.from("region\nEast\nWest\n");

let app;
let name;
const page = () => app.page;
const pane = () => page().locator(".nb-res");

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["new_notebook", "open_notebook", "save_notebook", "notebook_resources", "notebook_resource_add",
      "notebook_resource_rename", "notebook_resource_delete", "start_cell", "poll_cell"],
  });
  name = await page().evaluate(async () => {
    const nb = await rpc("new_notebook");
    loadIntoState(nb);
    state.showExplorer = true;
    state.explorerTab = "resources";
    go("notebook");
    return nb.name;
  });
});

after(async () => { if (app) await app.close(); });

const addFile = (buffer = CSV) => pane().locator('input[type="file"]').setInputFiles({ name: "regions.csv", mimeType: "text/csv", buffer });

test("an added file is listed with its size, above the snippets", { skip }, async () => {
  await pane().getByText(/No files yet/).waitFor();
  assert.ok(await page().locator(".explorer-content").getByText("Code snippets").isVisible());
  await addFile();
  await pane().locator('[data-file="regions.csv"]').waitFor();
  assert.match(await pane().locator('[data-file="regions.csv"]').innerText(), /regions\.csv\s+17 B/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-resources.png") });
});

test("adding a file of the same name asks for another name and keeps the first file", { skip }, async () => {
  await addFile(Buffer.from("other"));
  const dialog = page().locator(".dialog");
  await dialog.getByText("regions.csv is already here").waitFor();
  assert.strictEqual(await dialog.locator("input").inputValue(), "regions (2).csv");
  await dialog.getByRole("button", { name: "Add" }).click();
  await pane().locator('[data-file="regions (2).csv"]').waitFor();
  assert.match(await pane().locator('[data-file="regions.csv"]').innerText(), /17 B/);
});

test("Copy path gives builtin/<file>", { skip }, async () => {
  await page().evaluate(() => { window.__copied = null; navigator.clipboard.writeText = async (t) => { window.__copied = t; }; });
  await page().getByRole("button", { name: "Actions for regions.csv" }).click();
  await page().getByRole("menuitem", { name: "Copy path" }).click();
  await page().waitForFunction(() => window.__copied !== null);
  assert.strictEqual(await page().evaluate(() => window.__copied), "builtin/regions.csv");
});

test("Rename refuses an unsafe name, then renames", { skip }, async () => {
  await page().getByRole("button", { name: "Actions for regions (2).csv" }).click();
  await page().getByRole("menuitem", { name: "Rename" }).click();
  const dialog = page().locator(".dialog");
  await dialog.locator("input").fill("../east.csv");
  await dialog.getByRole("button", { name: "Rename" }).click();
  await dialog.getByText(/Avoid \//).waitFor();
  await dialog.locator("input").fill("east.csv");
  await dialog.getByRole("button", { name: "Rename" }).click();
  await pane().locator('[data-file="east.csv"]').waitFor();
  assert.strictEqual(await pane().locator('[data-file="regions (2).csv"]').count(), 0);
});

test("Delete asks first, in red, and Cancel keeps the file", { skip }, async () => {
  await page().getByRole("button", { name: "Actions for east.csv" }).click();
  await page().getByRole("menuitem", { name: "Delete" }).click();
  const dialog = page().locator(".dialog");
  await dialog.getByText("Delete east.csv?").waitFor();
  assert.ok(await dialog.locator(".danger-solid").isVisible());
  assert.strictEqual(await page().evaluate(() => document.activeElement.textContent), "Cancel");
  await page().keyboard.press("Enter");
  assert.strictEqual(await pane().locator('[data-file="east.csv"]').count(), 1);
  await page().getByRole("button", { name: "Actions for east.csv" }).click();
  await page().getByRole("menuitem", { name: "Delete" }).click();
  await dialog.locator(".danger-solid").click();
  await page().waitForFunction(() => !document.querySelector('[data-file="east.csv"]'));
});

test("a cell reads the file as builtin/<file>", { skip }, async () => {
  const text = await page().evaluate(async (nb) => {
    await rpc("start_cell", { code: 'print(open("builtin/regions.csv").read().split())', notebook: nb });
    let snap = await rpc("poll_cell", { version: -1, wait: 2 });
    while (snap.status === "running") snap = await rpc("poll_cell", { version: snap.version, wait: 2 });
    return snap.outputs.map((o) => o.text || o.evalue || "").join("");
  }, name);
  assert.match(text, /\['region', 'East', 'West'\]/);
  assert.deepStrictEqual(app.errors, []);
});

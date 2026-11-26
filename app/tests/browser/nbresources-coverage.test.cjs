// Notebook resources beyond notebook-resources.test.cjs: a folder that can't be read, a notebook at
// the 100-file limit, a file over 50 MB, each name the Rename box refuses, Show in folder and Open
// folder, and the "Show notebook resources" command. The engine's replies are mocked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const pane = () => page().locator(".nb-res");
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);
const db = { files: [], fail: null };

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      readiness_check: () => [],
      notebook_resources: () => {
        if (db.fail) throw new Error(db.fail);
        return { files: db.files, folder: "C:/Users/chris/KratosForge/notebooks/Orders.resources/builtin" };
      },
      notebook_resource_add: ({ file_name }) => { db.files.push({ name: file_name, size: 4 }); return { added: true, name: file_name }; },
      notebook_resource_rename: ({ file_name, new_name }) => {
        db.files = db.files.map((f) => (f.name === file_name ? { ...f, name: new_name } : f));
        return { name: new_name };
      },
      notebook_resource_reveal: () => ({ ok: true }),
    },
  });
  await page().waitForFunction(() => typeof loadIntoState === "function" && !!document.querySelector(".rail-item"));
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  db.files = [{ name: "regions.csv", size: 17 }];
  db.fail = null;
  app.calls.length = 0;
  await page().evaluate(() => {
    closeDialog();
    document.getElementById("toast").textContent = "";
    loadIntoState({ name: "Orders", cells: [] });
    state.showExplorer = true;
    state.explorerTab = "resources";
    go("notebook");
  });
});

// Waits for the engine to have been asked `n` times; fails if it isn't within 10 s.
async function callsMade(method, n) {
  for (const end = Date.now() + 10000; calls(method).length < n; await new Promise((r) => setTimeout(r, 20))) {
    if (Date.now() > end) throw new Error(`${method} was called ${calls(method).length} times, not ${n}`);
  }
}
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);

test("a resources folder that can't be read says why and offers no files", { skip }, async () => {
  db.fail = "PermissionError: Access is denied";
  await page().evaluate(() => { state.notebook.resourcesView = null; render(); });
  await pane().locator(".error-text").waitFor();
  assert.match(await pane().locator(".error-text").innerText(), /Access is denied/);
  assert.strictEqual(await pane().locator(".nb-res-row").count(), 0);
});

test("at 100 files Add files is off and says why", { skip }, async () => {
  db.files = Array.from({ length: 100 }, (_, i) => ({ name: `part-${i}.csv`, size: 10 }));
  await page().evaluate(() => { state.notebook.resourcesView = null; render(); });
  await pane().getByText("100 of 100").waitFor();
  const add = pane().getByRole("button", { name: "Add files" });
  assert.ok(await add.isDisabled());
  assert.strictEqual(await add.getAttribute("title"), "This notebook has 100 resource files, the most it can have.");
});

test("a file over 50 MB isn't sent; a small one beside it is added", { skip }, async () => {
  await pane().locator('[data-file="regions.csv"]').waitFor();
  await page().evaluate(() => {
    const input = document.querySelector('.nb-res input[type="file"]');
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array(51 * 1024 * 1024)], "big.parquet"));
    dt.items.add(new File(["a,b\n"], "small.csv"));
    input.files = dt.files;
    input.dispatchEvent(new Event("change"));
  });
  await pane().locator('[data-file="small.csv"]').waitFor();
  assert.deepStrictEqual(calls("notebook_resource_add").map((c) => c.file_name), ["small.csv"]);
  await toastSays("File added to the notebook's resources");
  assert.strictEqual(await pane().locator('[data-file="big.parquet"]').count(), 0);
});

test("Rename says what's wrong with each unusable name, then renames", { skip }, async () => {
  await page().getByRole("button", { name: "Actions for regions.csv" }).click();
  await page().getByRole("menuitem", { name: "Rename" }).click();
  const dialog = page().locator(".dialog");
  const box = dialog.locator("input");
  const refused = async (name, why) => {
    await box.fill(name);
    await dialog.getByRole("button", { name: "Rename" }).click();
    await dialog.getByText(why, { exact: true }).waitFor();
  };
  await refused("..", "Give the file a name.");
  await refused("east.", "A name can't end with a dot or a space on Windows.");
  await refused("CON.csv", "That name is kept by Windows for devices. Pick another.");
  await refused(`${"x".repeat(121)}`, "Keep the name to 120 characters.");
  assert.deepStrictEqual(calls("notebook_resource_rename"), []);
  await box.fill("east.csv");
  await dialog.getByRole("button", { name: "Rename" }).click();
  await pane().locator('[data-file="east.csv"]').waitFor();
  assert.deepStrictEqual(calls("notebook_resource_rename"), [{ name: "Orders", file_name: "regions.csv", new_name: "east.csv" }]);
  await toastSays("Renamed. Cells now read it as builtin/east.csv.");
});

test("Show in folder reveals the file, Open folder the folder", { skip }, async () => {
  await page().getByRole("button", { name: "Actions for regions.csv" }).click();
  await page().getByRole("menuitem", { name: "Show in folder" }).click();
  await callsMade("notebook_resource_reveal", 1);
  await pane().getByRole("button", { name: "Open folder" }).click();
  await callsMade("notebook_resource_reveal", 2);
  assert.deepStrictEqual(calls("notebook_resource_reveal"), [{ name: "Orders", file_name: "regions.csv" }, { name: "Orders" }]);
});

test("the command Show notebook resources opens the explorer on Resources", { skip }, async () => {
  await page().evaluate(() => { state.showExplorer = false; state.explorerTab = "snippets"; go("notebook"); });
  await page().keyboard.press("Control+k");
  await page().keyboard.type("Show notebook resources");
  await page().locator("#palette .palette-item.on", { hasText: "Show notebook resources" }).waitFor();
  await page().keyboard.press("Enter");
  await pane().locator('[data-file="regions.csv"]').waitFor();
  assert.deepStrictEqual(await page().evaluate(() => [state.showExplorer, state.explorerTab]), [true, "resources"]);
  assert.deepStrictEqual(app.errors, []);
});

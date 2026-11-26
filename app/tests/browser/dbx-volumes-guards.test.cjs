// Workspaces > Databricks > Volumes on this PC (dbx-volumes.js, Preview) guard paths beyond dbx-volumes.test.cjs:
// the volumes or a folder that can't be read, each with Try again; a folder opened and left by its breadcrumb; a
// New volume the engine refuses, which keeps the dialog open with the reason; and Download of several files into a
// folder, where files already there are named in a red confirm and kept unless you replace them.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ROOT = "C:\\Users\\you\\AppData\\Local\\FabricDesktop\\volumes\\main";
const vol = (schema, name, items) => ({ name, catalog: "main", database: schema, owner: "you", storage_location: "",
  volume_type: "MANAGED", comment: null, path: `/Volumes/main/${schema}/${name}`, folder: `${ROOT}\\${schema}\\${name}`, items, created: 1 });
const NOW = Date.now();
const file = (dir, name, extra = {}) => ({ path: `dbfs:${dir}/${name}`, name, size: 1024, modification_time: NOW, is_dir: false, ...extra });
const LANDING = "/Volumes/main/sales/landing";
const DAY = `${LANDING}/2026-11-22`;

const world = {};
function reset() {
  Object.assign(world, {
    volumes: [vol("sales", "landing", 3)],
    files: {
      [LANDING]: [file(LANDING, "2026-11-22", { size: 0, is_dir: true }), file(LANDING, "a.csv"), file(LANDING, "b.csv")],
      [DAY]: [file(DAY, "late.csv")],
    },
    volumesFail: false, filesFail: new Set(), createFail: null, already: new Set(),
  });
}

let app;
const page = () => app.page;
const calls = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const view = () => page().locator("#view .dbx-vol");
const dialog = () => page().locator(".dialog");
const toast = () => page().locator("#toast");
const frames = async (until) => {
  for (let i = 0; i < 200 && !(await until()); i++) await page().evaluate(() => new Promise((r) => requestAnimationFrame(r)));
};

async function open(volume = "sales.landing") {
  app.calls.length = 0;
  await page().evaluate((volume) => { Object.assign(dbxVol, { volume, parts: [] }); dbxVol.picked.clear(); go("home"); go("databricks_volumes"); }, volume);
}

before(async () => {
  if (skip) return;
  reset();
  app = await openApp({
    viewport: { width: 1280, height: 900 },
    mocks: {
      app_settings: () => ({ show_preview: { value: true, source: "default" } }),
      databricks_volumes: () => {
        if (world.volumesFail) throw new Error("The volumes folder is locked.");
        return { root: ROOT, volumes: world.volumes };
      },
      databricks_volume_files: ({ path }) => {
        if (world.filesFail.has(path)) throw new Error("Access is denied.");
        return { path, entries: world.files[path] || [], truncated: false };
      },
      databricks_volume_create: ({ schema, volume }) => {
        if (world.createFail) throw new Error(world.createFail);
        const v = vol(schema, volume, 0); world.volumes = [...world.volumes, v]; return v;
      },
      databricks_volume_download: ({ path, save_to, overwrite }) => (world.already.has(path) && !overwrite ? { exists: true } : { saved_to: save_to, size: 5 }),
    },
  });
  await page().waitForFunction(() => typeof renderDatabricksVolumes === "function" && !!document.querySelector(".rail-item"));
  await page().evaluate(() => {
    const invoke = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (cmd, args) => (cmd === "pick_file" || cmd === "pick_folder"
      ? (window.__fdShellCalls.push({ cmd, args }), window.__fdPicked) : invoke(cmd, args));
  });
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  reset();
  app.errors.length = 0;
  await page().evaluate(() => { closeDialog?.(); window.__fdPicked = null; document.querySelector("#toast")?.replaceChildren(); });
});

test("volumes that can't be read say so, and Try again reads them again", { skip }, async () => {
  world.volumesFail = true;
  await open();
  await view().getByText("The volumes on this PC couldn't be read.").waitFor();
  assert.strictEqual(calls("databricks_volumes").length, 1);
  assert.deepStrictEqual(calls("databricks_volume_files"), []);
  world.volumesFail = false;
  await view().getByRole("button", { name: "Try again" }).click();
  await view().locator(".dbx-vol-detail table").waitFor();
  assert.strictEqual(calls("databricks_volumes").length, 2);
  assert.deepStrictEqual(app.errors, []);
});

test("a folder that can't be listed says so, and Try again lists it", { skip }, async () => {
  world.filesFail.add(LANDING);
  await open();
  await view().getByText(`${LANDING} couldn't be listed.`).waitFor();
  assert.strictEqual(await view().locator(".dbx-vol-detail table").count(), 0);
  world.filesFail.clear();
  await view().locator(".dbx-vol-detail").getByRole("button", { name: "Try again" }).click();
  await view().locator(".dbx-vol-detail table").waitFor();
  assert.deepStrictEqual(calls("databricks_volume_files").map((p) => p.path), [LANDING, LANDING]);
  assert.deepStrictEqual(app.errors, []);
});

test("a folder opens from its row, and the breadcrumb goes back to the volume", { skip }, async () => {
  await open();
  await view().locator(".dbx-vol-detail table").waitFor();
  await view().getByLabel("Select a.csv").check();
  await view().locator(".dbx-vol-folder", { hasText: "2026-11-22/" }).click();
  await view().getByText("late.csv").waitFor();
  assert.deepStrictEqual(await page().evaluate(() => [dbxVol.parts, dbxVol.picked.size]), [["2026-11-22"], 0], "a ticked file doesn't follow into the folder");
  await view().locator(".dbx-vol-crumb").first().click();
  await view().getByText("a.csv").waitFor();
  assert.deepStrictEqual(await page().evaluate(() => dbxVol.parts), []);
  assert.deepStrictEqual(calls("databricks_volume_files").map((p) => p.path), [LANDING, DAY, LANDING]);
  assert.deepStrictEqual(app.errors, []);
});

test("New volume: a refusal keeps the dialog open with the reason, and Create works again", { skip }, async () => {
  world.createFail = "A volume called main.sales.landing is already on this PC.";
  await open();
  await view().locator(".dbx-vol-detail table").waitFor();
  await page().getByRole("button", { name: "New volume" }).click();
  await dialog().waitFor();
  await dialog().getByLabel("Schema").fill("sales");
  await dialog().getByLabel("Volume name").fill("landing");
  await dialog().getByRole("button", { name: "Create" }).click();
  const problem = dialog().locator(".error-text");
  await problem.waitFor();
  assert.strictEqual(await problem.innerText(), "A volume called main.sales.landing is already on this PC.");
  assert.ok(await dialog().getByRole("button", { name: "Create" }).isEnabled(), "Create can be pressed again");
  world.createFail = null;
  await dialog().getByLabel("Volume name").fill("inbox");
  await dialog().getByRole("button", { name: "Create" }).click();
  await view().locator(".dbx-vol-item.on", { hasText: "inbox" }).waitFor();
  assert.strictEqual(await dialog().count(), 0);
  assert.deepStrictEqual(calls("databricks_volume_create"), [{ schema: "sales", volume: "landing", comment: null }, { schema: "sales", volume: "inbox", comment: null }]);
  assert.deepStrictEqual(app.errors, []);
});

async function downloadBoth() {
  await open();
  await view().locator(".dbx-vol-detail table").waitFor();
  await page().evaluate(() => { window.__fdPicked = "C:\\Users\\you\\Downloads\\"; });
  await view().getByLabel("Select a.csv").check();
  await view().getByLabel("Select b.csv").check();
  await view().getByRole("button", { name: "Download", exact: true }).click();
}

test("Download of several files: one already there is named in a red confirm, and Cancel keeps it", { skip }, async () => {
  world.already.add(`dbfs:${LANDING}/a.csv`);
  await downloadBoth();
  await dialog().getByText("Replace a.csv?").waitFor();
  assert.match(await dialog().innerText(), /Files of these names are already in C:\\Users\\you\\Downloads\\:[\s\S]*a\.csv/);
  assert.match(await dialog().getByRole("button", { name: "Replace" }).getAttribute("class"), /danger/);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await toast().getByText("Saved 1 file to C:\\Users\\you\\Downloads\\").waitFor();
  assert.deepStrictEqual(calls("databricks_volume_download"), [
    { path: `dbfs:${LANDING}/a.csv`, save_to: "C:\\Users\\you\\Downloads\\a.csv" },
    { path: `dbfs:${LANDING}/b.csv`, save_to: "C:\\Users\\you\\Downloads\\b.csv" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Download of several files: Replace saves over the ones already there", { skip }, async () => {
  world.already = new Set([`dbfs:${LANDING}/a.csv`, `dbfs:${LANDING}/b.csv`]);
  await downloadBoth();
  await dialog().getByText("Replace 2 files?").waitFor();
  await dialog().getByRole("button", { name: "Replace" }).click();
  await toast().getByText("Saved 2 files to C:\\Users\\you\\Downloads\\").waitFor();
  assert.deepStrictEqual(calls("databricks_volume_download").filter((p) => p.overwrite).map((p) => p.save_to),
    ["C:\\Users\\you\\Downloads\\a.csv", "C:\\Users\\you\\Downloads\\b.csv"]);
  assert.deepStrictEqual(app.errors, []);
});

test("Download into a folder does nothing when the folder dialog is closed", { skip }, async () => {
  await open();
  await view().locator(".dbx-vol-detail table").waitFor();
  await view().getByLabel("Select a.csv").check();
  await view().getByLabel("Select b.csv").check();
  await view().getByRole("button", { name: "Download", exact: true }).click();
  await frames(async () => (await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "pick_folder").length)) > 0);
  await page().evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  assert.deepStrictEqual(calls("databricks_volume_download"), []);
  assert.deepStrictEqual(app.errors, []);
});

// Workspaces > Databricks > Volumes on this PC (dbx-volumes.js, Preview): the approved mockup's states (a volume with
// files and one ticked, an empty volume, no volumes yet, the delete confirm, a narrow window), and each action's engine
// call: New volume, Upload (asking before it replaces), New folder, Download, Delete and Drop volume (both red confirms
// that say it can't be undone), Rename and the comment. Every engine call and the file dialog are mocked.
// axe covers dialogs (#1673, ui.test.cjs): the cases below run axe while these are open.
// axe covers dialogs: dbx-volumes.js:dbxVolCreate
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ROOT = "C:\\Users\\you\\AppData\\Local\\FabricDesktop\\volumes\\main";
const vol = (schema, name, items, comment = null) => ({ name, catalog: "main", database: schema, owner: "you", storage_location: "",
  volume_type: "MANAGED", comment, path: `/Volumes/main/${schema}/${name}`, folder: `${ROOT}\\${schema}\\${name}`, items, created: 1 });
const NOW = Date.now();
const FILES = [
  { path: "dbfs:/Volumes/main/sales/landing/2026-11-22", name: "2026-11-22", size: 0, modification_time: NOW - 86400000, is_dir: true },
  { path: "dbfs:/Volumes/main/sales/landing/orders_2026-11-23.parquet", name: "orders_2026-11-23.parquet", size: 212 * 1024 * 1024, modification_time: NOW, is_dir: false },
  { path: "dbfs:/Volumes/main/sales/landing/customers.csv", name: "customers.csv", size: 1.8 * 1024 * 1024, modification_time: NOW, is_dir: false },
];

const world = {};
function reset() {
  Object.assign(world, { volumes: [vol("finance", "raw", 12), vol("sales", "exports", 0), vol("sales", "landing", 3)],
    files: { "/Volumes/main/sales/landing": FILES }, uploadExists: true, picked: null });
}
reset();

let app;
const page = () => app.page;
const calls = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const view = () => page().locator("#view .dbx-vol");
const dialog = () => page().locator(".dialog");

async function open() {
  app.calls.length = 0;
  await page().evaluate(() => { Object.assign(dbxVol, { volume: "", parts: [] }); dbxVol.picked.clear(); go("home"); go("databricks_volumes"); });
  await view().locator(".dbx-vol-split").waitFor();
}

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 900 },
    mocks: {
      app_settings: () => ({ show_preview: { value: true, source: "default" } }),
      databricks_volumes: () => ({ root: ROOT, volumes: world.volumes }),
      databricks_volume_files: ({ path }) => ({ path, entries: world.files[path] || [], truncated: false }),
      databricks_volume_create: ({ schema, volume }) => { const v = vol(schema, volume, 0); world.volumes = [...world.volumes, v]; return v; },
      databricks_volume_drop: ({ schema, volume }) => {
        world.volumes = world.volumes.filter((v) => !(v.database === schema && v.name === volume));
        return { dropped: `main.${schema}.${volume}` };
      },
      databricks_volume_rename: ({ schema, volume, new_name }) => {
        const v = world.volumes.find((x) => x.database === schema && x.name === volume);
        Object.assign(v, vol(schema, new_name, v.items));
        return v;
      },
      databricks_volume_describe: ({ schema, volume }) => world.volumes.find((x) => x.database === schema && x.name === volume),
      databricks_volume_set_comment: ({ schema, volume, comment }) => Object.assign(world.volumes.find((x) => x.database === schema && x.name === volume), { comment }),
      databricks_volume_upload: ({ source, path, overwrite }) => (world.uploadExists && !overwrite
        ? { exists: true, path: `${path}/${source.split("\\").pop()}` } : { path: `${path}/${source.split("\\").pop()}`, size: 5 }),
      databricks_volume_new_folder: ({ path }) => ({ path }),
      databricks_volume_download: ({ save_to }) => ({ saved_to: save_to, size: 5 }),
      databricks_volume_delete: ({ paths }) => ({ deleted: paths }),
      databricks_volume_reveal: ({ path }) => ({ shown: path }),
    },
  });
  await page().waitForFunction(() => typeof renderDatabricksVolumes === "function" && !!document.querySelector(".rail-item"));
  await page().evaluate(() => {
    const invoke = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (cmd, args) => (cmd === "pick_file" || cmd === "pick_folder"
      ? (window.__fdShellCalls.push({ cmd, args }), window.__fdPicked) : invoke(cmd, args));
  });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

const shellCalls = (cmd) => page().evaluate((c) => window.__fdShellCalls.filter((x) => x.cmd === c).map((x) => x.args), cmd);

test("a volume with files: the mockup's view, its copy, and one file ticked", { skip }, async () => {
  reset();
  await open();
  const pageText = await page().locator("#view").innerText();
  assert.match(pageText, /Synced folders\s*Volumes on this PC\s*Preview/);
  assert.match(pageText, /This PC\s*Unity Catalog volumes for notebooks and SQL on this PC\. They're folders here, not the workspace's volumes, and nothing is uploaded to Databricks\./);
  assert.match(await page().locator(".subtabs .tab.active").innerText(), /^Databricks/);
  assert.deepStrictEqual(await view().locator(".dbx-vol-tree li").allInnerTexts(),
    ["main.finance", "raw\n12 items", "main.sales", "exports\n0 items", "landing\n3 items"]);
  assert.strictEqual(await view().locator(".dbx-vol-item.on").innerText(), "raw\n12 items");  // the first listed is picked
  await view().getByRole("button", { name: /^landing/ }).click();
  await view().locator("table.dbx-vol-files").waitFor();
  const detail = view().locator(".dbx-vol-detail");
  assert.match(await detail.innerText(), /main\.sales\.landing\s*Managed volume/);
  assert.match(await detail.innerText(), /Path\s*\/Volumes\/main\/sales\/landing\s*Copy path/);
  assert.match(await detail.innerText(), /On this PC\s*C:\\Users\\you\\AppData\\Local\\FabricDesktop\\volumes\\main\\sales\\landing\s*Show on this PC/);
  assert.deepStrictEqual(await detail.locator("thead th").allInnerTexts(), ["", "Name", "Size", "Modified"]);
  const rows = await detail.locator("tbody tr").allInnerTexts();
  assert.match(rows[0], /2026-11-22\/\s*Yesterday /);
  assert.match(rows[1], /orders_2026-11-23\.parquet\s*212 MB\s*Today /);
  assert.match(rows[2], /customers\.csv\s*1\.8 MB\s*Today /);
  for (const name of ["Download", "Delete"]) assert.ok(await detail.getByRole("button", { name, exact: true }).isDisabled(), name);
  await detail.getByLabel("Select orders_2026-11-23.parquet").check();
  assert.strictEqual(await detail.locator("tr.sel").count(), 1);
  for (const name of ["Upload", "New folder", "Download", "Delete"]) assert.ok(await detail.getByRole("button", { name, exact: true }).isEnabled(), name);
  await detail.getByRole("button", { name: "Show on this PC" }).click();
  for (let i = 0; i < 100 && !calls("databricks_volume_reveal").length; i++) await page().evaluate(() => new Promise((r) => requestAnimationFrame(r)));
  assert.deepStrictEqual(calls("databricks_volume_reveal"), [{ path: "/Volumes/main/sales/landing" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Delete asks in red, names the file and says it can't be undone, then deletes it", { skip }, async () => {
  reset();
  await page().evaluate(() => { dbxVol.volume = "sales.landing"; });
  app.calls.length = 0;
  await page().evaluate(() => { go("home"); go("databricks_volumes"); });
  const detail = view().locator(".dbx-vol-detail");
  await detail.getByLabel("Select orders_2026-11-23.parquet").check();
  await detail.getByRole("button", { name: "Delete", exact: true }).click();
  await dialog().waitFor();
  const text = await dialog().innerText();
  assert.match(text, /Delete orders_2026-11-23\.parquet\?/);
  assert.match(text, /It's removed from \/Volumes\/main\/sales\/landing and from its folder on this PC:/);
  assert.match(text, /orders_2026-11-23\.parquet \(212 MB\)/);
  assert.match(text, /This can't be undone\. A notebook or query that reads the file will fail\./);
  assert.match(await dialog().getByRole("button", { name: "Delete" }).getAttribute("class"), /danger-solid/);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(calls("databricks_volume_delete"), []);
  await detail.getByRole("button", { name: "Delete", exact: true }).click();
  await dialog().getByRole("button", { name: "Delete" }).click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  for (let i = 0; i < 100 && !calls("databricks_volume_delete").length; i++) await page().evaluate(() => new Promise((r) => requestAnimationFrame(r)));
  assert.deepStrictEqual(calls("databricks_volume_delete"), [{ paths: ["dbfs:/Volumes/main/sales/landing/orders_2026-11-23.parquet"], confirm: true }]);
});

test("an empty volume and no volumes at all say how to fill them", { skip }, async () => {
  reset();
  await page().evaluate(() => { dbxVol.volume = "sales.exports"; });
  app.calls.length = 0;
  await page().evaluate(() => { go("home"); go("databricks_volumes"); });
  await view().getByText("This volume is empty").waitFor();
  assert.match(await view().locator(".dbx-vol-detail .dbx-vol-empty").innerText(),
    /This volume is empty\s*Upload files, or write to \/Volumes\/main\/sales\/exports from a notebook\./);
  world.volumes = [];
  await open();
  assert.match(await view().locator(".dbx-vol-list").innerText(),
    /No volumes yet\s*Create one with CREATE VOLUME main\.sales\.landing in a SQL cell, or with New volume\./);
  assert.match(await view().locator(".dbx-vol-detail").innerText(), /Pick a volume\s*Volumes you create on this PC show here\./);
  assert.deepStrictEqual(calls("databricks_volume_files"), []);
});

test("New volume: the dialog passes axe and creates the volume, which is then picked", { skip }, async () => {
  reset();
  await open();
  await page().getByRole("button", { name: "New volume" }).click();
  await dialog().waitFor();
  assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  await dialog().getByLabel("Volume name").fill(" inbox ");
  await dialog().getByLabel("Comment").fill("drops");
  await dialog().getByRole("button", { name: "Create" }).click();
  await view().locator(".dbx-vol-item.on", { hasText: "inbox" }).waitFor();
  assert.deepStrictEqual(calls("databricks_volume_create"), [{ schema: "default", volume: "inbox", comment: "drops" }]);
});

test("Upload asks before replacing; New folder and Download send the folder and the file picked", { skip }, async () => {
  reset();
  await page().evaluate(() => { dbxVol.volume = "sales.landing"; window.__fdPicked = "C:\\Data\\orders.csv"; });
  app.calls.length = 0;
  await page().evaluate(() => { go("home"); go("databricks_volumes"); });
  const detail = view().locator(".dbx-vol-detail");
  await detail.locator("table").waitFor();
  await detail.getByRole("button", { name: "Upload" }).click();
  await dialog().getByText("Replace orders.csv?").waitFor();
  await dialog().getByRole("button", { name: "Replace" }).click();
  for (let i = 0; i < 100 && calls("databricks_volume_upload").length < 2; i++) await page().evaluate(() => new Promise((r) => requestAnimationFrame(r)));
  assert.deepStrictEqual(calls("databricks_volume_upload"), [
    { source: "C:\\Data\\orders.csv", path: "/Volumes/main/sales/landing" },
    { source: "C:\\Data\\orders.csv", path: "/Volumes/main/sales/landing", overwrite: true }]);
  await view().locator(".dbx-vol-detail table").waitFor();

  await view().getByRole("button", { name: "New folder" }).click();
  await dialog().locator("input").fill("2026-11-24");
  await dialog().getByRole("button", { name: "Create" }).click();
  for (let i = 0; i < 100 && !calls("databricks_volume_new_folder").length; i++) await page().evaluate(() => new Promise((r) => requestAnimationFrame(r)));
  assert.deepStrictEqual(calls("databricks_volume_new_folder"), [{ path: "/Volumes/main/sales/landing/2026-11-24" }]);
  await view().locator(".dbx-vol-detail table").waitFor();

  await page().evaluate(() => { window.__fdPicked = "C:\\Users\\you\\Downloads\\customers.csv"; });
  await view().getByLabel("Select customers.csv").check();
  await view().getByRole("button", { name: "Download", exact: true }).click();
  for (let i = 0; i < 100 && !calls("databricks_volume_download").length; i++) await page().evaluate(() => new Promise((r) => requestAnimationFrame(r)));
  assert.deepStrictEqual((await shellCalls("pick_file")).at(-1), { start: null, saveName: "customers.csv" });
  assert.deepStrictEqual(calls("databricks_volume_download"), [{ path: "dbfs:/Volumes/main/sales/landing/customers.csv",
    save_to: "C:\\Users\\you\\Downloads\\customers.csv", overwrite: true }]);
  // A folder can't be downloaded whole.
  await view().getByLabel("Select 2026-11-22").check();
  assert.ok(await view().getByRole("button", { name: "Download", exact: true }).isDisabled());
});

test("Drop volume asks in red and says it can't be undone; Rename and the comment go to the engine", { skip }, async () => {
  reset();
  await page().evaluate(() => { dbxVol.volume = "finance.raw"; });
  app.calls.length = 0;
  await page().evaluate(() => { go("home"); go("databricks_volumes"); });
  const actions = () => view().getByRole("button", { name: "Actions for main.finance.raw" });
  await actions().click();
  await page().getByRole("menuitem", { name: "Add comment" }).click();
  await dialog().locator("input").fill("from the ERP");
  await dialog().getByRole("button", { name: "Save" }).click();
  await view().getByText("from the ERP").waitFor();
  assert.deepStrictEqual(calls("databricks_volume_describe"), [{ schema: "finance", volume: "raw" }]);
  assert.deepStrictEqual(calls("databricks_volume_set_comment"), [{ schema: "finance", volume: "raw", comment: "from the ERP" }]);

  await actions().click();
  await page().getByRole("menuitem", { name: "Drop volume" }).click();
  await dialog().waitFor();
  const text = await dialog().innerText();
  assert.match(text, /Drop main\.finance\.raw\?/);
  assert.match(text, /This can't be undone\. A notebook or query that reads the volume will fail\./);
  assert.match(await dialog().getByRole("button", { name: "Drop volume" }).getAttribute("class"), /danger-solid/);
  await dialog().getByRole("button", { name: "Drop volume" }).click();
  await view().locator(".dbx-vol-item.on", { hasText: "exports" }).waitFor();  // the first left is picked
  assert.deepStrictEqual(calls("databricks_volume_drop"), [{ schema: "finance", volume: "raw", confirm: true }]);

  await view().getByRole("button", { name: "Actions for main.sales.exports" }).click();
  await page().getByRole("menuitem", { name: "Rename" }).click();
  await dialog().locator("input").fill("outbox");
  await dialog().getByRole("button", { name: "Rename" }).click();
  await view().locator(".dbx-vol-item.on", { hasText: "outbox" }).waitFor();
  assert.deepStrictEqual(calls("databricks_volume_rename"), [{ schema: "sales", volume: "exports", new_name: "outbox" }]);
});

test("a narrow window stacks the volume list above the files", { skip }, async () => {
  reset();
  await open();
  await page().setViewportSize({ width: 820, height: 900 });
  try {
    const list = await view().locator(".dbx-vol-list").boundingBox();
    const detail = await view().locator(".dbx-vol-detail").boundingBox();
    assert.ok(detail.y >= list.y + list.height, `${JSON.stringify(list)} ${JSON.stringify(detail)}`);
  } finally {
    await page().setViewportSize({ width: 1280, height: 900 });
  }
});

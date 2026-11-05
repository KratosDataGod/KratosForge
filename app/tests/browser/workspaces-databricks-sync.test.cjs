// Workspaces > Databricks sync (Preview, dbx-sync.js): compare shows each file's state with conflicts
// unpushable, Push sends only the ticked files, a delete is off until ticked and asks again (Cancel
// sends nothing), and a .ipynb's losses are listed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.SCREENSHOT_DIR;
const HOST = "https://adb-1234567890123456.7.azuredatabricks.net";
const REMOTE = "/Workspace/Users/dev@contoso.com/sales-etl";
const ROWS = [
  { file: "load_orders.py", remote: "load_orders", here: "Changed", there: "Same as last sync", action: "push", note: "", losses: [] },
  { file: "silver_customers.ipynb", remote: "silver_customers", here: "New", there: "Not there", action: "push", note: "",
    losses: ["notebookutils (mssparkutils) is Fabric's. Databricks has dbutils."] },
  { file: "orders_dq.py", remote: "orders_dq", here: "Changed", there: "Changed there", action: "conflict", note: "Changed in Databricks too: compare and pick before pushing.", losses: [] },
  { file: "scratch.py", remote: "scratch", here: "Deleted", there: "Still there", action: "delete", note: "Off unless you tick it: deleting asks again.", losses: [] },
];

let app;
const page = () => app.page;
const pushes = () => app.calls.filter((c) => c.method === "databricks_sync_push").map((c) => c.params);

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 900 },
    engine: ["app_settings"],
    mocks: {
      engine_connections: () => ({ connections: [{ key: "cli:dev", label: "dev (Databricks CLI)", source: "cli", host: HOST }] }),
      databricks_sync_pairs: () => ({ pairs: [{ id: "s-1", name: "sales-etl", connection: "cli:dev", local: "C:\\repos\\sales-etl\\notebooks", remote: REMOTE, synced: 3 }] }),
      databricks_sync_compare: () => ({ schema: "databricks-sync@1", remote_exists: true, rows: ROWS, skipped: [], compared_at: 1 }),
      databricks_sync_push: (p) => ({ done: [...p.files.map((f) => ({ file: f, action: "pushed" })), ...p.deletes.map((f) => ({ file: f, action: "deleted" }))], skipped: [] }),
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof renderDatabricksSync === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

test("the Databricks tab compares and shows each file's state; a conflict can't be pushed", { skip }, async () => {
  await page().evaluate(() => go("databricks_sync"));
  await page().locator(".dbx-sync-table").waitFor({ timeout: 10000 });
  assert.match(await page().locator('.subtabs [role="tab"][aria-selected="true"]').textContent(), /Databricks/);
  const rows = await page().locator(".dbx-sync-table tbody tr").allTextContents();
  assert.strictEqual(rows.length, 4);
  assert.match(rows[1], /silver_customers\.ipynb.*New.*Not there.*notebookutils/);
  assert.match(rows[2], /orders_dq\.py.*Changed there.*conflict.*compare and pick/);
  assert.ok(await page().locator('input[aria-label="Push load_orders.py"]').isChecked());
  assert.ok(await page().locator('input[aria-label="orders_dq.py can\'t be pushed"]').isDisabled());
  assert.ok(!(await page().locator('input[aria-label="Delete scratch in Databricks"]').isChecked()));
  assert.match(await page().locator(".dbx-changes button", { hasText: /^Push/ }).textContent(), /Push 2 changes/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "workspaces-databricks-sync.png") });
});

test("a ticked delete asks again, Cancel sends nothing, and the confirm sends it with the push", { skip }, async () => {
  await page().locator('input[aria-label="Delete scratch in Databricks"]').check();
  await page().locator(".dbx-changes button", { hasText: "Push 3 changes" }).click();
  await page().locator("#dialog-root .dialog").waitFor();
  assert.match(await page().locator("#dialog-root .dialog").textContent(), /Delete 1 file in Databricks\?.*scratch will be deleted from \/Workspace\/Users\/dev@contoso\.com\/sales-etl/);
  await page().locator("#dialog-root button", { hasText: "Cancel" }).click();
  assert.deepStrictEqual(pushes(), []);
  await page().locator(".dbx-changes button", { hasText: "Push 3 changes" }).click();
  await page().locator("#dialog-root .dialog button", { hasText: "Delete in Databricks" }).click();
  for (let i = 0; i < 60 && !pushes().length; i++) await page().waitForTimeout(50);
  assert.deepStrictEqual(pushes(), [{ pair: "s-1", files: ["load_orders.py", "silver_customers.ipynb"], deletes: ["scratch.py"], confirm_deletes: true }]);
});

test("without a delete, a .ipynb's losses are confirmed and nothing asks to delete", { skip }, async () => {
  await page().locator(".dbx-sync-table").waitFor();
  await page().locator(".dbx-changes button", { hasText: "Push 2 changes" }).click();
  await page().locator("#dialog-root .dialog").waitFor();
  assert.match(await page().locator("#dialog-root .dialog").textContent(), /Push 2 files to Databricks\?.*silver_customers\.ipynb loses something/);
  await page().locator("#dialog-root .dialog button", { hasText: /^Push$/ }).click();
  for (let i = 0; i < 60 && pushes().length < 2; i++) await page().waitForTimeout(50);
  assert.deepStrictEqual(pushes()[1], { pair: "s-1", files: ["load_orders.py", "silver_customers.ipynb"], deletes: [], confirm_deletes: false });
});

test("Workspaces > Databricks has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await page().locator(".dbx-sync-table").waitFor();
  assert.deepStrictEqual(await axeFindings(page(), "#view .dbx-sync"), []);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

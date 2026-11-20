// Workspaces > Databricks sync (dbx-sync.js) beyond the push flow in workspaces-databricks-sync.test.cjs:
// the synced folders can't be read; no pairs and no Databricks connection; pairing a folder (Cancel, then
// Pair, which saves the pair and compares it); a workspace folder that isn't there yet, files left out and
// an empty folder; a compare that fails; switching pairs; Pull for a file changed only in Databricks; a
// push that skips a file; and Forget, which asks first. Every engine call is mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const HOST = "https://adb-1234567890123456.7.azuredatabricks.net";
const SALES = { id: "s-1", name: "sales-etl", connection: "cli:dev", local: "C:\\repos\\sales-etl\\notebooks", remote: "/Workspace/Users/dev@contoso.com/sales-etl", synced: 3 };
const HR = { id: "s-2", name: "hr-reports", connection: "cli:dev", local: "C:\\repos\\hr\\notebooks", remote: "/Workspace/Users/dev@contoso.com/hr", synced: 0 };
const SALES_ROWS = [
  { file: "load_orders.py", remote: "load_orders", here: "Changed", there: "Same as last sync", action: "push", note: "", losses: [] },
  { file: "gold_kpis.sql", remote: "gold_kpis", here: "Same", there: "Changed there", action: "none", note: "Changed in Databricks: pull to take it.", losses: [] },
  { file: "explore.ipynb", remote: "explore", here: "Same", there: "Changed there", action: "none", note: "", losses: [] },
  { file: "orders_dq.py", remote: "orders_dq", here: "Same", there: "Same", action: "none", note: "", losses: [] },
];

// What each mock answers; tests change it.
const world = {};
function reset() {
  Object.assign(world, { pairsFail: false, pairs: [], conns: [], compare: {}, compareFail: false, pushSkipped: [] });
}
reset();

let app;
const page = () => app.page;
const calls = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const view = () => page().locator("#view .dbx-sync");
// Waits (a frame at a time, no sleeps) until the engine has had n calls of m.
async function waitCalls(m, n) {
  for (let i = 0; i < 300 && calls(m).length < n; i++) await page().evaluate(() => new Promise((r) => requestAnimationFrame(r)));
  assert.strictEqual(calls(m).length, n, `${m} called ${n} time(s)`);
}

async function open() {
  app.calls.length = 0;
  await page().evaluate(() => { Object.assign(dbxSync, { pair: "", compare: null, adding: false, busy: false }); go("home"); go("databricks_sync"); });
  await page().locator(".dbx-sync .dbx-sync-cols, .dbx-sync > p").first().waitFor();
}

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 900 },
    mocks: {
      app_settings: () => ({ show_preview: { value: true, source: "default" } }),
      engine_connections: () => ({ connections: world.conns }),
      databricks_sync_pairs: () => {
        if (world.pairsFail) throw new Error("PermissionError: the sync settings file is locked by another program");
        return { pairs: world.pairs };
      },
      databricks_sync_pair_save: (p) => { const pair = { id: "s-new", ...p, synced: 0 }; world.pairs = [...world.pairs, pair]; return pair; },
      databricks_sync_pair_remove: ({ pair }) => { world.pairs = world.pairs.filter((p) => p.id !== pair); return { removed: true }; },
      databricks_sync_compare: ({ pair }) => {
        if (world.compareFail) throw new Error("Databricks answered 403: this token can't read /Workspace/Users/dev@contoso.com/hr");
        return { schema: "databricks-sync@1", remote_exists: true, rows: [], skipped: [], compared_at: 1, ...world.compare[pair] };
      },
      databricks_sync_push: (p) => ({ done: p.files.slice(world.pushSkipped.length).map((f) => ({ file: f, action: "pushed" })),
        skipped: world.pushSkipped }),
      databricks_sync_pull: (p) => ({ done: p.files.map((f) => ({ file: f, action: "pulled" })) }),
    },
  });
  await page().waitForFunction(() => typeof renderDatabricksSync === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

test("synced folders that can't be read say so, and the error is toasted", { skip }, async () => {
  reset();
  world.pairsFail = true;
  await open();
  await view().getByText("The synced folders couldn't be read.").waitFor();
  assert.match(await page().locator("#toast").innerText(), /Couldn't read the synced folders/);
  assert.strictEqual(await view().locator(".dbx-pairs").count(), 0);
});

test("with no pair and no Databricks connection, pairing points to Settings > Connections", { skip }, async () => {
  reset();
  world.conns = [{ key: "pat:other", label: "a token with no host", source: "pat" }];  // not a Databricks connection
  await open();
  await view().getByText("Pair a folder to see what would change.").waitFor();
  await view().getByRole("button", { name: "Pair a folder" }).click();
  await view().getByText("Add a Databricks connection on Settings > Connections first.").waitFor();
  assert.deepStrictEqual(calls("databricks_sync_compare"), []);
});

test("Pair a folder: Cancel closes the form; Pair saves the trimmed fields, then compares the new pair", { skip }, async () => {
  reset();
  world.conns = [{ key: "cli:dev", label: "dev (Databricks CLI)", source: "cli", host: HOST },
    { key: "cli:prod", label: "prod (Databricks CLI)", source: "cli", host: HOST }];
  world.compare["s-new"] = { remote_exists: false, rows: [], skipped: ["bad:name.py", "huge.ipynb"] };
  await open();
  await view().getByRole("button", { name: "Pair a folder" }).click();
  const form = view().locator("form.dbx-add");
  await form.waitFor();
  assert.deepStrictEqual(await form.getByLabel("Databricks connection").locator("option").allTextContents(),
    ["dev (Databricks CLI)", "prod (Databricks CLI)"]);
  await form.getByRole("button", { name: "Cancel" }).click();
  await view().getByRole("button", { name: "Pair a folder" }).waitFor();
  assert.strictEqual(await view().locator("form.dbx-add").count(), 0);

  await view().getByRole("button", { name: "Pair a folder" }).click();
  await form.getByLabel("Pair name").fill("  finance-etl ");
  await form.getByLabel("Databricks connection").selectOption("cli:prod");
  await form.getByRole("textbox", { name: "Folder on this PC", exact: true }).fill(" C:\\repos\\finance\\notebooks ");
  await form.getByLabel("Path in the Databricks workspace").fill(" /Workspace/Users/dev@contoso.com/finance ");
  await form.getByRole("button", { name: "Pair", exact: true }).click();
  await view().getByRole("heading", { name: "Changes in finance-etl" }).waitFor();
  assert.deepStrictEqual(calls("databricks_sync_pair_save"), [{ name: "finance-etl", connection: "cli:prod",
    local: "C:\\repos\\finance\\notebooks", remote: "/Workspace/Users/dev@contoso.com/finance" }]);
  // The new pair is chosen and compared: a folder not there yet, files left out, nothing to push.
  await view().getByText("The workspace folder isn't there yet: the first push makes it.").waitFor();
  assert.deepStrictEqual(calls("databricks_sync_compare"), [{ pair: "s-new" }]);
  assert.strictEqual(await view().locator(".dbx-pair[aria-pressed='true'] strong").innerText(), "finance-etl");
  assert.match(await view().locator(".dbx-changes").innerText(), /Left out: bad:name\.py, huge\.ipynb \(a name with odd characters/);
  await view().getByText("No notebooks (.py, .ipynb, .sql) in this folder.").waitFor();
  assert.ok(await view().getByRole("button", { name: "Push 0 changes" }).isDisabled());
  assert.strictEqual(await view().locator("form.dbx-add").count(), 0);
});

test("a compare that fails says the workspace couldn't be compared, and Compare again asks again", { skip }, async () => {
  reset();
  world.pairs = [SALES, HR];
  world.compareFail = true;
  await open();
  await view().getByText("The workspace couldn't be compared.").waitFor();
  assert.match(await page().locator("#toast").innerText(), /Couldn't compare with Databricks/);
  world.compareFail = false;
  world.compare["s-1"] = { rows: SALES_ROWS };
  await view().getByRole("button", { name: "Compare again" }).click();
  await view().locator(".dbx-sync-table").waitFor();
  assert.deepStrictEqual(calls("databricks_sync_compare"), [{ pair: "s-1" }, { pair: "s-1" }]);
});

test("switching pairs compares the one picked", { skip }, async () => {
  reset();
  world.pairs = [SALES, HR];
  world.compare = { "s-1": { rows: SALES_ROWS }, "s-2": { rows: [] } };
  await open();
  await view().locator(".dbx-sync-table").waitFor();
  await view().locator(".dbx-pair", { hasText: "hr-reports" }).click();
  await view().getByRole("heading", { name: "Changes in hr-reports" }).waitFor();
  await view().getByText("No notebooks (.py, .ipynb, .sql) in this folder.").waitFor();
  assert.strictEqual(await view().locator(".dbx-pair", { hasText: "hr-reports" }).getAttribute("aria-pressed"), "true");
  assert.strictEqual(await view().locator(".dbx-pair", { hasText: "sales-etl" }).getAttribute("aria-pressed"), "false");
  assert.deepStrictEqual(calls("databricks_sync_compare"), [{ pair: "s-1" }, { pair: "s-2" }]);
});

test("a file changed only in Databricks can be pulled; a .ipynb can't", { skip }, async () => {
  reset();
  world.pairs = [SALES];
  world.compare = { "s-1": { rows: SALES_ROWS } };
  await open();
  await view().locator(".dbx-sync-table").waitFor();
  assert.ok(await view().getByLabel("explore.ipynb can't be pushed").isDisabled());
  assert.ok(await view().getByLabel("orders_dq.py can't be pushed").isDisabled());
  assert.match(await view().locator("tr.dbx-none", { hasText: "orders_dq.py" }).innerText(), /In step/);
  assert.strictEqual(await view().getByRole("button", { name: /^Pull/ }).count(), 0);
  await view().getByLabel("Pull gold_kpis.sql").check();
  await view().getByRole("button", { name: "Pull 1 from Databricks" }).click();
  await page().locator("#toast", { hasText: "1 file pulled from Databricks." }).waitFor();
  assert.deepStrictEqual(calls("databricks_sync_pull"), [{ pair: "s-1", files: ["gold_kpis.sql"] }]);
  // It compares again afterwards.
  await view().locator(".dbx-sync-table").waitFor();
  await waitCalls("databricks_sync_compare", 2);
});

test("unticking the only push leaves nothing to push; a push that skips a file says which and why", { skip }, async () => {
  reset();
  world.pairs = [SALES];
  world.compare = { "s-1": { rows: SALES_ROWS } };
  await open();
  await view().locator(".dbx-sync-table").waitFor();
  await view().getByLabel("Push load_orders.py").uncheck();
  assert.ok(await view().getByRole("button", { name: "Push 0 changes" }).isDisabled());
  await view().getByLabel("Push load_orders.py").check();
  world.pushSkipped = [{ file: "load_orders.py", why: "changed in Databricks a moment ago" }];
  await view().getByRole("button", { name: "Push 1 change", exact: true }).click();
  await page().locator("#toast", { hasText: "0 changes pushed; 1 skipped: load_orders.py (changed in Databricks a moment ago)." }).waitFor();
  assert.deepStrictEqual(calls("databricks_sync_push"), [{ pair: "s-1", files: ["load_orders.py"], deletes: [], confirm_deletes: false }]);
});

test("Forget asks first: Cancel keeps the pair, Forget removes it and changes no file", { skip }, async () => {
  reset();
  world.pairs = [SALES];
  world.compare = { "s-1": { rows: SALES_ROWS } };
  await open();
  await view().locator(".dbx-sync-table").waitFor();
  await view().getByRole("button", { name: "Forget this pair" }).click();
  const dialog = page().locator("#dialog-root .dialog");
  await dialog.waitFor();
  assert.match(await dialog.innerText(), /Forget sales-etl\?[\s\S]*No file changes here or in Databricks\./);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(calls("databricks_sync_pair_remove"), []);
  await view().getByRole("button", { name: "Forget this pair" }).click();
  await dialog.getByRole("button", { name: "Forget" }).click();
  await view().getByText("Pair a folder to see what would change.").waitFor();
  assert.deepStrictEqual(calls("databricks_sync_pair_remove"), [{ pair: "s-1" }]);
  assert.deepStrictEqual(calls("databricks_sync_push"), []);
  assert.strictEqual(await view().locator(".dbx-pair").count(), 0);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

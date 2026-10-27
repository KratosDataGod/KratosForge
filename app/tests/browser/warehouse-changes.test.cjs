// WH-10b: changes to a warehouse from the Warehouse page, in a real browser (see harness.cjs for how
// to run it). Runs are mocked: no warehouse, sign-in or Fabric is needed. The mock's dry run says what
// the engine's would (sql_exec.py, tested in test_warehouse_queries.py).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const DEV = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const PROD = { kind: "warehouse", workspace_id: "ws9", workspace_name: "Sales Prod", item_id: "wh9", name: "WH_Gold" };
// No environment in the project: its name reads like Test, but a name is only a guess (#807).
const UNMAPPED = { kind: "warehouse", workspace_id: "ws5", workspace_name: "kf_tenant_test", item_id: "wh5", name: "WH_Try" };
const SHOTS = process.env.SCREENSHOT_DIR;
const UPDATE = "UPDATE dbo.DimCustomer SET Segment = 'Enterprise' WHERE AnnualRevenue >= 10000000;\nGO\nEXEC dbo.usp_RefreshSegments;";

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const starts = () => app.calls.filter((c) => c.method === "sql_start").map((c) => c.params);

function plan(query) {
  if (/BEGIN TRAN/i.test(query)) {
    return { dry_run: true, permission: "fabric_mutation", statements: [], refused: { statement: 1, permission: "fabric_mutation",
      reason: "the run is one transaction the app commits: leave out BEGIN TRAN, COMMIT and ROLLBACK" } };
  }
  if (/^\s*(UPDATE|EXEC|CREATE)/im.test(query)) {
    return { dry_run: true, permission: "warehouse_write", refused: null, statements: [
      { statement: 1, permission: "warehouse_write", reason: "UPDATE dbo.DimCustomer changes the warehouse" },
      { statement: 2, permission: "warehouse_write", reason: "EXEC dbo.usp_RefreshSegments changes the warehouse" }] };
  }
  return { dry_run: true, permission: "read_only", refused: null, statements: [{ statement: 1, permission: "read_only", reason: "reads" }] };
}

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks(() => ({ columns: [{ name: "x", type: "int" }], rows: [[1]] }));
  const changed = new Set();  // runs started as a change: their status says so, as the engine's does
  app = await openApp({
    engine: ["sql_format", "sql_dialects", "sql_analyze", "sql_queries_list"],
    mocks: {
      meta_project: () => ({ environments: { dev: { workspace_id: "ws1" }, prod: { workspace_id: "ws9" } } }),
      ...runs,
      sql_start: (p) => {
        if (p.dry_run) return plan(p.query);
        const write = /^\s*(UPDATE|EXEC|CREATE)/im.test(p.query);
        if (write && !(p.allow || []).includes("warehouse_write")) {
          return { id: "r0", state: "failed", target: "warehouse", error_kind: "permission", rows: 0, columns: [],
            error: "Statement 1 was refused (UPDATE isn't a query that only reads), so nothing was sent. A warehouse run only reads.",
            refused: { statement: 1, permission: "fabric_mutation", label: "would change something outside this run" } };
        }
        const s = { ...runs.sql_start(p), target: "warehouse" };
        if (write) changed.add(s.id);
        return write ? { ...s, change: true, affected: 1284, committed: true } : s;
      },
      sql_status: (p) => ({ ...runs.sql_status(p), target: "warehouse",
        ...(changed.has(p.execution) ? { change: true, affected: 1284, committed: true } : {}) }),
    },
  });
  await page().evaluate((wh) => openWarehouseQuery(wh), DEV);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

async function setDoc(text) {
  await page().evaluate((text) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: text.length } });
  }, text);
}
const run = () => page().getByRole("button", { name: "Run", exact: true }).click();
const allow = () => page().getByRole("checkbox", { name: "Allow changes" });

test("the page reads until Allow changes is ticked, and a change says where to tick", { skip }, async () => {
  assert.ok(!(await allow().isChecked()), "off when the page opens");
  assert.match(await page().locator(".sql-capacity-note").textContent(), /reads only/);
  await setDoc(UPDATE);
  await run();
  await page().getByRole("dialog").getByRole("button", { name: "Run in Fabric" }).click();  // the first-run confirm
  await page().waitForFunction(() => /tick Allow changes/.test(document.querySelector(".warehouse-page")?.textContent || ""));
  const last = starts().at(-1);
  assert.ok(!last.allow && !last.confirm && !last.dry_run, "sent as a read, which the engine refuses");
});

test("a change shows what it changes, then runs confirmed and named, in one transaction", { skip }, async () => {
  await allow().check();
  assert.match(await page().locator(".sql-capacity-note").textContent(), /changes after you confirm/);
  await setDoc(UPDATE);
  const before = starts().length;
  await run();
  const dialog = page().getByRole("dialog", { name: "Change WH_Gold?" });
  await dialog.waitFor();
  const text = await dialog.textContent();
  assert.match(text, /Sales Dev.*WH_Gold.*Dev/s);
  assert.match(text, /Statement 1: UPDATE dbo\.DimCustomer changes the warehouse.*Statement 2: EXEC dbo\.usp_RefreshSegments/s);
  assert.match(text, /one transaction/);
  assert.strictEqual(await dialog.getByRole("textbox", { name: "Warehouse name" }).count(), 0, "Dev doesn't ask for the name");
  assert.strictEqual(await page().evaluate(() => document.activeElement?.textContent), "Cancel", "Enter doesn't run the change");
  assert.strictEqual(starts().length, before + 1, "only the dry run so far");
  assert.ok(starts().at(-1).dry_run && starts().at(-1).allow[0] === "warehouse_write");
  await dialog.getByRole("button", { name: "Run the change" }).click();
  await page().waitForFunction(() => /1,284 rows affected, committed/.test(document.querySelector(".toast")?.textContent || ""));
  const sent = starts().filter((p) => p.confirm).at(-1);  // the catalog is read again after it
  assert.deepStrictEqual([sent.allow, sent.confirm, sent.confirm_name, sent.dry_run], [["warehouse_write"], true, "WH_Gold", undefined]);
  await page().getByRole("tab", { name: "Messages" }).click();
  assert.match(await page().locator(".sql-messages").textContent(), /Change: 1,284 rows affected · committed/);
  const h = await page().evaluate(() => sqlHistory(sqlPage.ref)[0]);
  assert.deepStrictEqual([h.change, h.affected], [true, 1284]);
});

test("Cancel sends nothing, and a script that only reads runs as before", { skip }, async () => {
  await setDoc(UPDATE);
  const before = starts().length;
  await run();
  await page().getByRole("dialog", { name: "Change WH_Gold?" }).getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(starts().length, before + 1, "the dry run only");
  await setDoc("SELECT TOP 10 * FROM dbo.DimCustomer");
  await run();
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].exec?.state === "completed");
  const sent = starts().at(-1);
  assert.ok(!sent.allow && !sent.confirm && !sent.dry_run, "a read is sent as a read");
});

test("a script the engine would refuse shows why and sends nothing", { skip }, async () => {
  await setDoc("BEGIN TRAN;\nUPDATE dbo.t SET a = 1;");
  const before = starts().length;
  await run();
  await page().waitForFunction(() => /leave out BEGIN TRAN.*nothing was sent/.test(document.querySelector(".warehouse-page")?.textContent || ""));
  assert.strictEqual(starts().length, before + 1, "the dry run only");
});

test("in Prod the warehouse's name has to be typed", { skip }, async () => {
  await page().evaluate((wh) => openWarehouseQuery(wh), PROD);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  assert.ok(!(await allow().isChecked()), "each warehouse is ticked on its own");
  await allow().check();
  await setDoc(UPDATE);
  await run();
  const dialog = page().getByRole("dialog", { name: "Change WH_Gold in Prod?" });
  await dialog.waitFor();
  const go = dialog.getByRole("button", { name: "Run in Prod" });
  assert.ok(await go.isDisabled());
  await dialog.getByRole("textbox", { name: "Warehouse name" }).fill("WH_Gol");
  if (SHOTS) {
    await page().waitForTimeout(500);  // the dialog fades in
    await page().screenshot({ path: path.join(SHOTS, "wh-10-change-prod.png") });
  }
  assert.ok(await go.isDisabled(), "a near miss isn't enough");
  await dialog.getByRole("textbox", { name: "Warehouse name" }).fill("WH_Gold");
  await go.click();
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].exec?.committed === true);
  assert.strictEqual(starts().filter((p) => p.confirm).at(-1).confirm_name, "WH_Gold");
});

test("a workspace with no environment is treated like Prod, whatever its name says", { skip }, async () => {
  await page().evaluate((wh) => openWarehouseQuery(wh), UNMAPPED);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  await allow().check();
  await setDoc(UPDATE);
  await run();
  const dialog = page().getByRole("dialog", { name: "Change WH_Try?" });
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /Not set, so it's treated like Prod/);
  const go = dialog.getByRole("button", { name: "Run the change" });
  assert.ok(await go.isDisabled(), "the name has to be typed");
  await dialog.getByRole("textbox", { name: "Warehouse name" }).fill("WH_Try");
  await go.click();
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].exec?.committed === true);
  assert.strictEqual(starts().filter((p) => p.confirm).at(-1).confirm_name, "WH_Try");
});

test("with changes turned off in Settings the switch can't be ticked", { skip }, async () => {
  await page().evaluate(() => { state.settings = { ...state.settings, sql_warehouse_writes: { value: false, source: "policy" } }; renderQuery(); });
  assert.ok(await allow().isDisabled() && !(await allow().isChecked()));
  assert.match(await page().locator(".sql-capacity-note").textContent(), /reads only/);
  await page().evaluate(() => { state.settings.sql_warehouse_writes = { value: true, source: "default" }; renderQuery(); });
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});

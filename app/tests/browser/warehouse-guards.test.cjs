// The Warehouse page's failure and waiting paths, in a real browser (see harness.cjs for how to run it):
// a definition that can't be read, a catalog query that queues before it finishes, a result that must be
// run again, and the arrow keys on a column. Runs are mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const CATALOG_COLS = ["schema_name", "object_name", "column_name", "type_name", "max_length", "precision", "scale", "is_nullable"]
  .map((name) => ({ name, type: "varchar" }));
const PROFILE_NAMES = ["n", "c0_null", "c0_distinct", "c0_min", "c0_max"];

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method);
// What the next profile query does: "rerun" (its result must be run again) or "queue" (it waits once
// first); paged: the catalog's results come one row per page.
const next = { profile: null, paged: false };
const queued = new Set();

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks((p) => {
    if (p.query.includes("sys.columns")) return { columns: CATALOG_COLS, rows: [["dbo", "Sales", "Amount", "decimal", 9, 18, 2, false]] };
    if (p.query.includes("sys.objects")) return { columns: CATALOG_COLS.slice(0, 3), rows: [["dbo", "Sales", "U"], ["dbo", "vSales", "V"]] };
    if (p.query.includes("FROM sys.sql_modules AS m\nWHERE")) throw new Error("Invalid object name 'dbo.vSales'.");
    if (p.query.includes("COUNT_BIG(*) AS n")) {
      return { columns: PROFILE_NAMES.map((name) => ({ name, type: "varchar" })), rows: [[42, 0, 40, "1.00", "9.00"]] };
    }
    return { columns: [{ name: "x", type: "int" }], rows: [[1]] };
  });
  app = await openApp({
    engine: ["sql_format", "sql_dialects", "sql_analyze"],
    mocks: {
      ...runs,
      sql_start: (p) => {
        const out = { ...runs.sql_start(p), target: "warehouse" };
        if (p.query.includes("COUNT_BIG(*) AS n") && next.profile === "queue") { queued.add(out.id); return { ...out, state: "queued" }; }
        return out;
      },
      sql_status: (p) => { queued.delete(p.execution); return { ...runs.sql_status(p), target: "warehouse" }; },
      sql_page: (p) => {
        if (next.profile === "rerun" && calls("sql_start").at(-1)?.params.query.includes("COUNT_BIG(*) AS n")) {
          return { rerun_needed: true, message: "The result is no longer kept. Run the query again." };
        }
        return runs.sql_page(next.paged ? { ...p, limit: 1 } : p);
      },
    },
  });
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  await page().getByRole("button", { name: "List objects" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Run in Fabric" }).click();
  await page().getByRole("button", { name: "Script Sales" }).waitFor();
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const toastText = () => page().evaluate(() => [...document.querySelectorAll(".toast")].map((t) => t.textContent).join("\n"));

test("Script as CREATE on a view whose definition can't be read says why and opens no tab", { skip }, async () => {
  const tabs = await page().evaluate(() => sqlPage.tabs.length);
  await page().getByRole("treeitem", { name: /^Views/ }).click();
  await page().getByRole("treeitem", { name: "vSales", exact: true }).click({ button: "right" });
  await page().getByRole("menuitem", { name: "Script as CREATE" }).click();
  await page().waitForFunction(() => /Could not script vSales: /.test([...document.querySelectorAll(".toast")].map((t) => t.textContent).join("\n")));
  assert.match(await toastText(), /Could not script vSales: Invalid object name 'dbo\.vSales'\./);
  assert.doesNotMatch(await toastText(), /Could not script vSales: Error:/, "the Error: prefix is dropped");
  assert.strictEqual(await page().evaluate(() => sqlPage.tabs.length), tabs, "no script tab opens");
});

test("Right on a column, which has nothing to open, leaves the focus where it is", { skip }, async () => {
  const focused = () => page().evaluate(() => document.activeElement?.dataset.tid);
  const sales = page().locator('[data-tid="o:dbo.Sales"]');
  if (await sales.getAttribute("aria-expanded") !== "true") await page().getByRole("button", { name: "Show columns of Sales" }).click();
  await page().locator('[data-tid="c:dbo.Sales.Amount"]').focus();
  await page().keyboard.press("ArrowRight");
  assert.strictEqual(await focused(), "c:dbo.Sales.Amount");
  assert.strictEqual(await sales.getAttribute("aria-expanded"), "true", "the table stays open");
});

test("a catalog that comes back a page at a time is read to its last page", { skip }, async () => {
  next.paged = true;
  const pages = calls("sql_page").length;
  await page().locator(".explorer-content").getByRole("link", { name: "Refresh" }).click();
  await page().waitForFunction(() => sqlPage.catalog?.state === "done" && sqlPage.catalog.objects.length === 2);
  next.paged = false;
  const objects = await page().evaluate(() => sqlPage.catalog.objects.map((o) => `${o.schema}.${o.name}`));
  assert.deepStrictEqual(objects.sort(), ["dbo.Sales", "dbo.vSales"]);
  assert.ok(calls("sql_page").slice(pages).some((c) => c.params.offset === 1), "the second page was asked for");
});

async function openDetail() {
  await page().getByRole("button", { name: "Script Sales" }).click();
  await page().getByRole("menuitem", { name: "Table detail" }).click();
  const dialog = page().getByRole("dialog", { name: "dbo.Sales" });
  await dialog.waitFor();
  return dialog;
}

test("a profile whose result must be run again says it couldn't profile, and Profile works again", { skip }, async () => {
  next.profile = "rerun";
  const dialog = await openDetail();
  const profile = dialog.getByRole("button", { name: "Profile" });
  await profile.click();
  await dialog.getByText(/Could not profile dbo\.Sales: /).waitFor();
  assert.match(await dialog.textContent(), /Could not profile dbo\.Sales: The result is no longer kept\. Run the query again\./);
  assert.strictEqual(await profile.isDisabled(), false, "Profile can be pressed again");
  next.profile = null;
  await page().keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
});

test("a profile that queues first is asked about until it finishes, then shows the counts", { skip }, async () => {
  next.profile = "queue";
  const statuses = calls("sql_status").length;
  const dialog = await openDetail();
  await dialog.getByRole("button", { name: "Profile" }).click();
  await dialog.getByText(/42 rows, counted in Fabric/).waitFor();
  assert.ok(calls("sql_status").length > statuses, "the queued run's status was asked for");
  assert.strictEqual(queued.size, 0);
  next.profile = null;
  await page().keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

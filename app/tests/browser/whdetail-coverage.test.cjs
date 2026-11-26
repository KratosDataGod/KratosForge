// Table detail on the Warehouse page (whdetail.js) beyond warehouse-detail.test.cjs: a table with no columns
// listed, a profile with more columns than are profiled and a count that came back empty, a profile that fails,
// the clone script with a past time, and a clone with no name. The profile's query to Fabric is replaced by the
// test (whCatalogQuery) with the shape sql_exec.py answers; nothing reaches Fabric.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "whd", name: "WH_Gold" };
const T = { schema: "dbo", name: "Sales" };

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {} });
  await page().evaluate((wh) => {
    sqlWarehouseOk.add(sqlRefKey(wh));  // the once-per-warehouse confirm was answered
    window.__opened = [];
    window.__answer = null;
    window.__whCatalogQuery = whCatalogQuery;
    whCatalogQuery = async () => { const a = window.__answer; if (a instanceof Error) throw a; return a; };
  }, WH);
});
after(async () => { await app?.close(); });
beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => { closeDialog(); toastClose(); window.__opened = []; });
  app.errors.length = 0;
});
const open = (cols) => page().evaluate(([wh, t, cols]) => whTableDetail(wh, t, cols, { open: (title, text) => window.__opened.push({ title, text }) }), [WH, T, cols]);
const dialog = () => page().getByRole("dialog", { name: "dbo.Sales" });

test("no columns listed says none; more than 50 columns says how many weren't profiled; an empty count shows no share", { skip }, async () => {
  await open(null);
  assert.match(await dialog().innerText(), /0 columns in WH_Gold\./);
  await page().evaluate(() => closeDialog());

  const cols = Array.from({ length: 52 }, (_, i) => ({ name: `c${i}`, type: i ? "varchar(20)" : "int", nullable: i !== 0 }));
  await page().evaluate(() => { window.__answer = { columns: ["n", "c0_null", "c0_distinct", "c0_min", "c0_max"].map((name) => ({ name })), rows: [] }; });
  await open(cols);
  await dialog().getByRole("button", { name: "Profile" }).click();
  await dialog().getByText(/The last 2 columns weren't profiled\.$/).waitFor();
  const first = await dialog().locator("tbody tr").first().locator("td").allInnerTexts();
  assert.deepStrictEqual(first, ["c0", "int NOT NULL", "0%", "–", "–"]);
  assert.deepStrictEqual(await dialog().locator("tbody tr").nth(51).locator("td").allInnerTexts(), ["c51", "varchar(20)", "", "", ""]);
  assert.deepStrictEqual(app.errors, []);
});

test("a profile that fails says why, and the button works again", { skip }, async () => {
  await page().evaluate(() => { window.__answer = new Error("The warehouse is paused"); });
  await open([{ name: "id", type: "int" }]);
  await dialog().getByRole("button", { name: "Profile" }).click();
  await dialog().getByText("Could not profile dbo.Sales: The warehouse is paused").waitFor();
  assert.strictEqual(await dialog().getByRole("button", { name: "Profile" }).isDisabled(), false);
  assert.deepStrictEqual(app.errors, []);
});

test("the clone script takes a past time; a clone needs a name", { skip }, async () => {
  await open([{ name: "id", type: "int" }]);
  await dialog().getByLabel("Clone's name").fill("");
  await dialog().getByRole("button", { name: "Script the clone" }).click();
  await page().waitForFunction(() => document.getElementById("toast").textContent === "Name the new table first.");
  await dialog().getByLabel("Clone's name").fill("Sales_then");
  await dialog().getByLabel("Clone as it was at (your time, optional)").fill("2026-11-01T09:30");
  await dialog().getByRole("button", { name: "Script the clone" }).click();
  const [opened] = await page().evaluate(() => window.__opened);
  const utc = await page().evaluate(() => whAsOfUtc("2026-11-01T09:30"));
  assert.strictEqual(opened.title, "Sales_then (CLONE)");
  assert.ok(opened.text.startsWith(`-- A clone of dbo.Sales as it was at ${utc} UTC: a new table that shares its files.`), opened.text);
  assert.match(opened.text, new RegExp(`AS CLONE OF \\[dbo\\]\\.\\[Sales\\] AT '${utc.replace(/\./g, "\\.")}';\\n$`));
  assert.deepStrictEqual(app.errors, []);
});

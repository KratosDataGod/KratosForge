// Saved and shared queries (WH-06) beyond saved-queries.test.cjs: a list that can't be read and Try
// again, a Git repository's shared queries with a file that can't be read and one from another
// warehouse, the filter with nothing matching, a delete the engine refuses, and a Save dialog whose
// save fails and then saves to the shared folder. The saved-query calls are mocked; nothing runs.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const PLACES = [{ id: "mine", label: "My queries" }, { id: "git:sales", label: "sales-sql" }];
const db = { listFails: 1, saveFails: 1, queries: [
  { place: "git:sales", place_label: "sales-sql", name: "Top customers.sql", title: "Top customers", description: "For the weekly call", warehouse: "WH_Gold", sql: "SELECT 1" },
  { place: "git:sales", place_label: "sales-sql", name: "Prod check.sql", title: "Prod check", description: "", warehouse: "WH_Prod", sql: "SELECT 2" },
  { place: "git:sales", place_label: "sales-sql", name: "Broken.sql", title: "Broken", description: "", warehouse: "", sql: "", problem: "The file isn't UTF-8." },
] };

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const aside = () => page().locator(".warehouse-page aside.explorer");
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast").textContent), re.source);

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_analyze"],
    mocks: {
      readiness_check: () => [],
      ...sqlRunMocks(() => ({ columns: [{ name: "x", type: "int" }], rows: [[1]] })),
      sql_queries_list: () => {
        if (db.listFails-- > 0) throw new Error("PermissionError: the sql-queries folder of sales-sql can't be read");
        return { queries: db.queries, places: PLACES, notes: ["sales-sql has changes that aren't committed."] };
      },
      sql_query_delete: () => { throw new Error("The file is open in another program."); },
      sql_query_save: ({ place, title }) => {
        if (db.saveFails-- > 0) throw new Error("Error: A query called Top customers is already there.");
        db.queries.push({ place, place_label: "sales-sql", name: `${title}.sql`, title, description: "", warehouse: "WH_Gold", sql: "SELECT 3" });
        return { place, name: `${title}.sql`, version: "0123456789abcdef" };
      },
    },
  });
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  app.errors.length = 0;
});
after(async () => { await app?.close(); });

test("a list that can't be read says why, and Try again reads it", { skip }, async () => {
  await aside().getByRole("tab", { name: "Queries" }).click();
  await aside().getByText("Could not list saved queries: PermissionError: the sql-queries folder of sales-sql can't be read").waitFor();
  assert.ok(await aside().getByLabel("Filter queries").isHidden());
  await aside().getByRole("button", { name: "Try again" }).click();
  await aside().locator(".wh-q-row", { hasText: "Top customers" }).waitFor();
  assert.strictEqual(calls("sql_queries_list").length, 2);
});

test("shared queries list under their repository, with another warehouse's and an unreadable one marked", { skip }, async () => {
  const text = await aside().locator(".wh-queries").textContent();
  assert.match(text, /My queries · on this PCNo saved queries yet\. Save query keeps the current tab here\.sales-sql · shared in Git/);
  assert.match(text, /sales-sql has changes that aren't committed\./);
  const prod = aside().locator(".wh-q-row", { hasText: "Prod check" }).locator(".tree-row").first();
  assert.strictEqual(await prod.locator(".wh-q-from").textContent(), "WH_Prod");
  assert.strictEqual(await prod.getAttribute("title"), "Saved from WH_Prod");
  const broken = aside().locator(".wh-q-row", { hasText: "Broken" }).locator(".tree-row").first();
  assert.ok(await broken.isDisabled());
  assert.strictEqual(await broken.getAttribute("title"), "The file isn't UTF-8.");
  assert.match(await broken.innerText(), /can't be read/);
  const top = aside().locator(".wh-q-row", { hasText: "Top customers" }).locator(".tree-row").first();
  assert.strictEqual(await top.getAttribute("title"), "For the weekly call · Saved from WH_Gold");
});

test("the filter matches the description and warehouse, and says when nothing matches", { skip }, async () => {
  const filter = aside().getByLabel("Filter queries");
  await filter.fill("weekly");
  await aside().locator(".wh-q-row", { hasText: "Prod check" }).waitFor({ state: "detached" });
  assert.deepStrictEqual(await aside().locator(".wh-q-row .label").allTextContents(), ["Top customers"]);
  await filter.fill("wh_prod");
  await aside().locator(".wh-q-row", { hasText: "Prod check" }).waitFor();
  await filter.fill("nothing like this");
  await aside().locator(".wh-q-row").first().waitFor({ state: "detached" });
  assert.match(await aside().locator(".wh-queries").textContent(), /My queries · on this PCNothing matches\./);
  await filter.fill("");
  await aside().locator(".wh-q-row", { hasText: "Top customers" }).waitFor();
});

test("a delete the engine refuses says why and keeps the query listed", { skip }, async () => {
  await aside().getByRole("button", { name: "Delete Top customers" }).click();
  const confirm = page().getByRole("dialog", { name: "Delete saved query" });
  assert.match(await confirm.innerText(), /the sql-queries folder of sales-sql; commit the change in Source control to remove it for your team/);
  await confirm.getByRole("button", { name: "Delete" }).click();
  await toastSays(/^Couldn't delete the query: The file is open in another program\.$/);
  await aside().locator(".wh-q-row", { hasText: "Top customers" }).waitFor();
  assert.deepStrictEqual(calls("sql_query_delete"), [{ place: "git:sales", name: "Top customers.sql" }]);
});

test("Save query says why a save failed and keeps the dialog; it then saves to the shared folder", { skip }, async () => {
  await page().getByRole("button", { name: "Save query" }).click();
  const dialog = page().getByRole("dialog", { name: "Save query" });
  await dialog.getByLabel("Name").fill("Top customers");
  await dialog.getByLabel("Save to").selectOption({ label: "sales-sql (Git: sql-queries folder)" });
  await dialog.getByRole("button", { name: "Save" }).click();
  await dialog.getByRole("alert").getByText("A query called Top customers is already there.").waitFor();
  assert.ok(await dialog.getByRole("button", { name: "Save" }).isEnabled(), "the person can try again");
  await dialog.getByLabel("Name").fill("Top customers 2026");
  await dialog.getByRole("button", { name: "Save" }).click();
  await toastSays(/^Saved Top customers 2026 to sales-sql\.$/);
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(calls("sql_query_save").map((c) => [c.place, c.title, c.warehouse]),
    [["git:sales", "Top customers", "WH_Gold"], ["git:sales", "Top customers 2026", "WH_Gold"]]);
  const file = await page().evaluate(() => sqlPage.tabs[sqlPage.active].file);
  assert.deepStrictEqual(file, { place: "git:sales", name: "Top customers 2026.sql", place_label: "sales-sql", title: "Top customers 2026",
    description: "", warehouse: "WH_Gold", version: "0123456789abcdef" });
  assert.deepStrictEqual(app.errors, []);
});

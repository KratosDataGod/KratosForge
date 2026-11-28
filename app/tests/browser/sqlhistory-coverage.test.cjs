// Query history on the Warehouse page (sqlhistory.js) beyond query-history.test.cjs: history that can't be read,
// an error with no text, storage that can't be listed, the shared budget emptying another warehouse's history,
// a full browser storage making room and a storage that refuses outright, and the History list's rows: a run
// from another day, a state the app doesn't name, SQL that is only comments, a change with no count, nothing
// matching the filter, and Clear history cancelled. Everything is in this PC's storage; nothing runs.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "whh", name: "WH_Hist" };
const OTHER = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "who", name: "WH_Other" };

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {} });
});
after(async () => { await app?.close(); });
beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => {
    closeDialog(); toastClose();
    Object.keys(localStorage).filter((k) => k.startsWith(SQL_HISTORY_PREFIX)).forEach((k) => localStorage.removeItem(k));
  });
  app.errors.length = 0;
});

test("history that can't be read is empty, and an error with no text keeps none", { skip }, async () => {
  const out = await page().evaluate((wh) => {
    localStorage.setItem(sqlHistoryKey(wh), "{not json");
    return [sqlHistory(wh), sqlHistoryError(""), sqlHistoryError("Invalid column 'secret' at 12")];
  }, WH);
  assert.deepStrictEqual(out, [[], undefined, "Invalid column '…' at #"]);
  assert.deepStrictEqual(app.errors, []);
});

test("storage that can't be listed records nothing; the budget empties another warehouse's history first", { skip }, async () => {
  const out = await page().evaluate(([wh, other]) => {
    const key = Storage.prototype.key;
    Storage.prototype.key = () => { throw new Error("blocked"); };
    try { sqlHistoryAdd(wh, { sql: "SELECT 1", at: 1, state: "completed" }); } finally { Storage.prototype.key = key; }
    const none = sqlHistory(wh).length;
    // One old run on the other warehouse that is most of the budget, then a run here that goes over it.
    localStorage.setItem(sqlHistoryKey(other), JSON.stringify([{ sql: "x".repeat(SQL_HISTORY_SQL), at: 0, state: "completed" }]));
    const big = "y".repeat(SQL_HISTORY_SQL);
    for (let i = 0; i < 25; i++) sqlHistoryAdd(wh, { sql: big, at: 10 + i, state: "completed" });
    return { none, other: localStorage.getItem(sqlHistoryKey(other)), here: sqlHistory(wh).length };
  }, [WH, OTHER]);
  assert.strictEqual(out.none, 0);
  assert.strictEqual(out.other, null, "the other warehouse's only run went first, so its key is gone");
  assert.ok(out.here > 0 && out.here < 25, `${out.here} runs kept here`);
  assert.deepStrictEqual(app.errors, []);
});

test("full storage drops the oldest run to make room; a storage that refuses outright keeps nothing new", { skip }, async () => {
  const out = await page().evaluate((wh) => {
    sqlHistoryAdd(wh, { sql: "SELECT first", at: 1, state: "completed" });
    sqlHistoryAdd(wh, { sql: "SELECT second", at: 2, state: "completed" });
    const set = Storage.prototype.setItem;
    let fails = 1;
    Storage.prototype.setItem = function (k, v) {
      if (k.startsWith(SQL_HISTORY_PREFIX) && fails-- > 0) { const e = new Error("full"); e.name = "QuotaExceededError"; throw e; }
      return set.call(this, k, v);
    };
    try { sqlHistoryAdd(wh, { sql: "SELECT new", at: 3, state: "completed" }); } finally { Storage.prototype.setItem = set; }
    const afterQuota = sqlHistory(wh).map((e) => e.sql);
    Storage.prototype.setItem = () => { throw new Error("SecurityError"); };
    try { sqlHistoryAdd(wh, { sql: "SELECT refused", at: 4, state: "completed" }); } finally { Storage.prototype.setItem = set; }
    return { afterQuota, afterRefused: sqlHistory(wh).map((e) => e.sql) };
  }, WH);
  assert.deepStrictEqual(out.afterQuota, ["SELECT new", "SELECT second"]);
  assert.deepStrictEqual(out.afterRefused, ["SELECT new", "SELECT second"]);
  assert.deepStrictEqual(app.errors, []);
});

test("the History list's rows, a filter that matches nothing, and Clear history cancelled", { skip }, async () => {
  await page().evaluate((wh) => {
    const day = Date.now() - 3 * 86400000;
    localStorage.setItem(sqlHistoryKey(wh), JSON.stringify([
      { sql: "-- only a note", at: day, state: "odd_state" },
      { sql: "UPDATE t SET a = 1", at: Date.now(), state: "completed", change: true },
      { sql: "SELECT 1", at: Date.now(), state: "completed", rows: undefined },
    ]));
    sqlPage.whHistoryFilter = "";
    const view = whHistory(wh, { open: () => {}, insight: () => {} });
    const host = document.createElement("div");
    host.id = "hist-host";
    host.append(view.el);
    document.body.append(host);
  }, WH);
  const rows = page().locator("#hist-host .wh-h-row");
  const text = await rows.allInnerTexts();
  assert.match(text[0], /^-- only a note\s+\w{3} .*odd_state$/);
  assert.match(text[1], /Change$/);
  assert.match(text[2], /0 rows$/);
  await page().locator("#hist-host .wh-filter").fill("nothing like this");
  await page().locator("#hist-host").getByText("Nothing matches.").waitFor();
  await page().locator("#hist-host .wh-filter").fill("");
  await page().locator("#hist-host").getByRole("button", { name: "Clear history" }).click();
  const d = page().getByRole("dialog", { name: "Clear history" });
  await d.waitFor();
  await d.getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(await page().evaluate((wh) => sqlHistory(wh).length, WH), 3);
  await page().evaluate(() => { sqlPage.whHistoryFilter = ""; document.getElementById("hist-host").remove(); });
  assert.deepStrictEqual(app.errors, []);
});

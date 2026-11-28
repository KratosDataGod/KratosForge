// Learn (learn.js) guard paths beyond learn.test.cjs and try-the-fix.test.cjs: a hint the engine
// can't give says why in a toast and leaves the hint area as it was; a lesson with two exercises
// switches between them with its tabs; and a Compare that fails before it runs says it couldn't
// compare, and still lets the fix be applied or put away.
const { describe, test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

describe("a lesson's exercises and hints", { skip }, () => {
  // The real engine serves the lessons and the lab; only the hint is mocked, so it can fail.
  const ENGINE = ["app_settings", "save_app_settings", "academy_catalog", "academy_readiness", "learn_catalog", "learn_module",
    "learn_lab_start", "learn_lab_save", "learn_lab_run", "learn_lab_check", "learn_lab_reset", "notebook_practice_rules", "sql_practice_rules"];
  let app;
  let hintFails = true;
  const page = () => app.page;
  const hintCalls = () => app.calls.filter((c) => c.method === "learn_lab_hint").map((c) => c.params);

  before(async () => {
    app = await openApp({ engine: ENGINE, mocks: {
      learn_lab_hint: ({ level }) => {
        if (hintFails) throw new Error("The lab's hints couldn't be read.");
        return { level, of: 2, text: `Hint text ${level}` };
      },
    } });
    await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
  });
  after(async () => { await app?.close(); });

  beforeEach(async () => {
    hintFails = true;
    app.errors.length = 0;
    await page().evaluate(() => { learnPage.hints = {}; learnPage.exercise = 0; });
    await page().evaluate(() => learnOpen("LM-02"));
    await page().locator(".learn-lesson h2").first().waitFor();
    await page().locator(".learn-hint").waitFor();
  });

  test("a hint the engine can't give shows why in a toast, and the hint area stays as it was", async () => {
    await page().locator(".learn-hint button", { hasText: "Show a hint" }).click();
    await page().locator("#toast", { hasText: "The lab's hints couldn't be read." }).waitFor();
    assert.strictEqual(await page().locator(".learn-hint b").count(), 0, "no hint was shown");
    assert.strictEqual(await page().locator(".learn-hint button", { hasText: "Show a hint" }).count(), 1);
    const asked = hintCalls().at(-1);
    assert.strictEqual(asked.module_id, "LM-02");
    assert.strictEqual(asked.level, 1);
    assert.deepStrictEqual(app.errors, []);
  });

  test("after a hint that worked, the next one is asked for at the next level", async () => {
    hintFails = false;
    await page().locator(".learn-hint button", { hasText: "Show a hint" }).click();
    await page().locator(".learn-hint b", { hasText: "Hint 1 of 2" }).waitFor();
    hintFails = true;
    await page().locator(".learn-hint button", { hasText: "Show the next hint" }).click();
    await page().locator("#toast", { hasText: "The lab's hints couldn't be read." }).waitFor();
    assert.strictEqual(hintCalls().at(-1).level, 2);
    assert.match(await page().locator(".learn-hint").innerText(), /Hint 1 of 2[\s\S]*Hint text 1/, "the hint already shown stays");
    assert.deepStrictEqual(app.errors, []);
  });

  test("a lesson with two exercises has a tab for each, and the second tab shows the second prompt", async () => {
    const tabs = page().locator(".subtabs[aria-label=Exercises] [role=tab]");
    assert.deepStrictEqual(await tabs.allTextContents(), ["Exercise 1", "Exercise 2"]);
    assert.strictEqual(await tabs.nth(0).getAttribute("aria-selected"), "true");
    const first = await page().evaluate(() => learnPage.exercise);
    await tabs.nth(1).click();
    await page().waitForFunction(() => document.querySelectorAll(".subtabs[aria-label=Exercises] [role=tab]")[1]?.getAttribute("aria-selected") === "true");
    assert.strictEqual(first, 0);
    assert.strictEqual(await page().evaluate(() => learnPage.exercise), 1);
    assert.strictEqual(await page().locator(".subtabs[aria-label=Exercises] [role=tab]").nth(0).getAttribute("aria-selected"), "false");
    assert.deepStrictEqual(app.errors, []);
  });
});

describe("Try the fix when Compare fails", { skip }, () => {
  const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
  const SQL = "SELECT region, SUM(amount) AS revenue\nFROM dbo.sales\nWHERE YEAR(order_date) = 2026\nGROUP BY region;";
  let app;
  const page = () => app.page;
  const sq002 = () => page().locator(".sql-compat [data-rule=SQ002]");

  before(async () => {
    const runs = sqlRunMocks(() => ({ columns: [{ name: "region", type: "string" }], rows: [["n"]] }));
    app = await openApp({
      engine: ["app_settings", "save_app_settings", "sql_format", "sql_analyze", "sql_practices", "sql_queries_list",
        "governance_practice_settings", "learn_catalog", "learn_rule_guides", "learn_dismissals", "learn_fixes"],
      mocks: { ...runs, sql_start: (p) => ({ ...runs.sql_start(p), target: "warehouse" }),
        sql_status: (p) => ({ ...runs.sql_status(p), target: "warehouse" }),
        learn_fix_compare: () => { throw new Error("The warehouse is paused."); },
        learn_fix_checked: () => ({ recorded: true }) },
    });
    await page().waitForFunction(() => state.settings && "learn_mode" in state.settings);
    await page().evaluate((wh) => openWarehouseQuery(wh), WH);
    await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
    await page().evaluate((wh) => {
      sqlPage.catalog = { key: sqlRefKey(wh), state: "done", objects: [{ schema: "dbo", name: "sales", type: "U" }],
        cols: { "dbo.sales": [{ name: "region", type: "varchar(20)" }, { name: "amount", type: "decimal(18,2)" }, { name: "order_date", type: "date" }] },
        at: new Date(), error: null };
    }, WH);
    await page().evaluate((text) => {
      const v = sqlPage.editor.view;
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: 0 } });
    }, SQL);
    await page().waitForFunction((text) => sqlPage.tabs[sqlPage.active].practices && sqlPage.tabs[sqlPage.active].compatText === text, SQL, { timeout: 10000 });
    await page().locator("button[data-practices]").click();
    await sq002().waitFor();
  });
  after(async () => { await app?.close(); });

  test("a Compare that fails says it couldn't compare, changes nothing, and Cancel puts the fix away", async () => {
    app.errors.length = 0;
    await sq002().getByRole("button", { name: "Try the fix" }).click();
    await sq002().locator(".learn-fix").waitFor();
    await sq002().getByRole("button", { name: "Compare before applying" }).click();
    await page().getByRole("dialog").getByRole("button", { name: "Run both" }).click();
    const status = sq002().locator(".learn-fix p.output.error[role=status]");
    await status.waitFor();
    assert.strictEqual(await status.innerText(), "Couldn't compare: The warehouse is paused.");
    assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), SQL, "a failed Compare changes nothing");
    assert.strictEqual(await sq002().getByRole("button", { name: "Apply", exact: true }).count(), 1, "Apply is still offered");
    await sq002().getByRole("button", { name: "Cancel" }).click();
    await sq002().locator(".learn-fix").waitFor({ state: "detached" });
    assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), SQL);
    assert.deepStrictEqual(app.errors, []);
  });
});

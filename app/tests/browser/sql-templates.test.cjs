// WH-04/05: SQL from a template on the Warehouse page, in a real browser (see harness.cjs for how to
// run it). Every template, filled with its defaults, is read by the real engine's Fabric Warehouse
// rules (sql_analyze); nothing runs and no warehouse is needed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const SHOTS = process.env.SCREENSHOT_DIR;

// What the rules say about each template. Most read clean; the rest are warnings or SQL the reader
// doesn't know, and none is an error. A template or rule change shows up here.
const EXPECT = {
  "truncate-reload": "review",  // TRUNCATE deletes every row
  "masking": "review",          // ALTER COLUMN is in preview
  "time-travel": "unverified",  // OPTION (FOR TIMESTAMP AS OF ...) isn't read by the checker
  "row-security": "unverified", // nor is CREATE SECURITY POLICY
};

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_dialects", "sql_analyze"],
    mocks: { ...sqlRunMocks(() => ({ columns: [{ name: "x", type: "int" }], rows: [[1]] })) },
  });
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page .sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

test("every template, filled with its defaults, has no errors under the Fabric Warehouse rules", { skip }, async () => {
  const out = await page().evaluate(async () => {
    const res = [];
    for (const t of SQL_TEMPLATES) {
      const sql = sqlTemplateFill(t.sql, {});
      for (const target of t.endpoint ? ["warehouse", "endpoint"] : ["warehouse"]) {
        const r = await rpc("sql_analyze", { query: sql, target });
        res.push({ id: t.id, target, status: r.outcome.status, errors: r.diagnostics.filter((d) => d.severity === "error").map((d) => d.rule),
          left: /<\w+\s*,/.test(sql) });
      }
    }
    return res;
  });
  assert.ok(out.length >= 25);
  for (const r of out) {
    assert.deepStrictEqual(r.errors, [], `${r.id} on ${r.target}`);
    assert.strictEqual(r.left, false, `${r.id} has no placeholder left`);
    assert.strictEqual(r.status, EXPECT[r.id] || "no_known_problems", `${r.id} on ${r.target}`);
  }
});

test("placeholders are listed once, checked for their kind and written safely", { skip }, async () => {
  const r = await page().evaluate(() => {
    const sql = "SELECT [<cols, columns, Id>] FROM [<t, sysname, T>] WHERE n = '<who, string, x>' AND [<t, sysname, T>].k > <n, int, 5>";
    return {
      params: sqlTemplateParams(sql),
      filled: sqlTemplateFill(sql, { cols: " Id , Customer Name", who: "O'Brien", n: "7" }),
      bad: sqlTemplateFill(sql, { t: "x]; DROP TABLE y; --" }),
      problems: [["sysname", "a]b"], ["sysname", ""], ["int", "1.5"], ["datatype", "varchar(50)"], ["datatype", "int; DROP"],
        ["datetime", "2026-01-01T00:00:00"], ["datetime", "yesterday"], ["columns", "a,,b"], ["string", "a\nb"]].map(([k, v]) => !!sqlTemplateProblem(k, v)),
      never: sqlTemplateParams("SELECT 1 WHERE a <> b AND c < d"),
    };
  });
  assert.deepStrictEqual(r.params.map((p) => p.name), ["cols", "t", "who", "n"]);
  assert.strictEqual(r.filled, "SELECT [Id], [Customer Name] FROM [T] WHERE n = 'O''Brien' AND [T].k > 7");
  assert.match(r.bad, /FROM \[<t, sysname, T>\]/, "a name with ] is left as the placeholder, never written");
  assert.deepStrictEqual(r.problems, [true, true, true, false, true, false, true, true, true]);
  assert.deepStrictEqual(r.never, [], "a <> b isn't a placeholder");
});

test("SQL from a template opens a filled script in a new tab and runs nothing", { skip }, async () => {
  const tabs = await page().evaluate(() => sqlPage.tabs.length);
  await page().getByRole("button", { name: "SQL from a template" }).click();
  const dialog = page().locator(".sqlt-dialog");
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /For WH_Gold · T-SQL for Fabric Warehouse/);
  await dialog.getByRole("button", { name: "Accelerators" }).click();
  await dialog.getByRole("button", { name: /SCD type 2 merge procedure/ }).click();
  const open = dialog.getByRole("button", { name: "Open in a new tab" });
  await dialog.getByLabel("target table").fill("Dim]Customer");
  assert.ok(await open.isDisabled());
  assert.match(await dialog.locator(".sqlt-why").allTextContents().then((t) => t.join(" ")), /without \]/);
  await dialog.getByLabel("target table").fill("DimClient");
  assert.ok(!(await open.isDisabled()));
  assert.match(await dialog.locator(".sqlt-preview").textContent(), /\[dbo\]\.\[DimClient\]/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "wh-04-templates.png") });
  await open.click();
  await page().waitForFunction((n) => sqlPage.tabs.length === n + 1, tabs);
  const t = await page().evaluate(() => ({ name: sqlPage.tabs[sqlPage.active].name, text: sqlPage.editor.text() }));
  assert.strictEqual(t.name, "SCD type 2 merge procedure");
  assert.match(t.text, /CREATE OR ALTER PROCEDURE \[dbo\]\.\[usp_MergeDimCustomer\][\s\S]*\[dbo\]\.\[DimClient\]/);
  assert.ok(!app.calls.some((c) => c.method === "sql_start"), "opening a template runs nothing");
  assert.strictEqual(await page().locator(".sqlt-dialog").count(), 0);
});

test("search finds templates across groups", { skip }, async () => {
  await page().getByRole("button", { name: "SQL from a template" }).click();
  const dialog = page().locator(".sqlt-dialog");
  await dialog.getByLabel("Search templates").fill("lakehouse");
  const cards = await dialog.locator(".sqlt-card b").allTextContents();
  assert.deepStrictEqual(cards, ["Table from a query (CTAS)", "Append from a lakehouse table"]);
  await dialog.getByLabel("Search templates").fill("nothing like this");
  assert.match(await dialog.locator(".sqlt-list").textContent(), /No template matches/);
  await page().keyboard.press("Escape");
  await page().evaluate(() => closeDialog());
});

test("a SQL analytics endpoint is offered only the templates that make sense there", { skip }, async () => {
  await page().evaluate(() => openSqlTemplates({ kind: "endpoint", name: "LH_Silver", workspace_id: "ws1", item_id: "lh1" }, () => {}));
  const dialog = page().locator(".sqlt-dialog");
  const groups = await dialog.locator(".sqlt-group").allTextContents();
  assert.deepStrictEqual(groups, ["Views and functions", "Stored procedures", "Security", "Accelerators"]);
  assert.match(await dialog.textContent(), /T-SQL for Fabric SQL analytics endpoint/);
  await page().evaluate(() => closeDialog());
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});

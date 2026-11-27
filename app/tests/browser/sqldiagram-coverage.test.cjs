// SQL page > Explorer > Diagram (sqldiagram.js) beyond sql-diagram.test.cjs: a table that links to
// itself, a table with more columns than are drawn, a schema with more tables than are drawn, an empty
// schema and one whose tables can't be read, dark mode, Fit, and opening a table from the keyboard.
// The diagram is opened with a source whose reads give the tables a warehouse's catalog would.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {} });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => { closeDialog(); delete document.documentElement.dataset.colorMode; window.__revealed = []; });
});

// Opens the diagram on the hr schema; wide, empty and broken are the other schemas.
const open = () => page().evaluate(() => {
  const col = (name) => ({ name, type: "int" });
  const tables = {
    hr: [{ schema: "hr", name: "employees", columns: ["employee_id", "manager_id", "region_id", ...Array.from({ length: 12 }, (_, i) => `attr_${i}`)].map(col) },
      { schema: "hr", name: "regions", columns: [col("code"), col("label")] }],
    wide: Array.from({ length: 45 }, (_, i) => ({ schema: "wide", name: `t${i}`, columns: [col("v")] })),
    empty: [],
  };
  openSqlDiagram({ kind: "warehouse", name: "WH_HR", schemas: ["hr", "wide", "empty", "broken"],
    tables: async (s) => { if (!tables[s]) throw new Error("Invalid object name 'broken.sys.tables'."); return tables[s]; },
    keys: async () => ({ pks: { "hr.employees": ["employee_id"] },
      fks: [{ from: "hr.employees", fromCol: "manager_id", to: "hr.employees", toCol: "employee_id" }] }),
    reveal: (t) => window.__revealed.push(t) });
});
const dialog = () => page().locator("#dialog-root .dialog");
const status = () => dialog().getByRole("status");
const svg = () => dialog().locator(".sql-diagram svg");

test("a table that links to itself is drawn with a loop, and its extra columns are counted", { skip }, async () => {
  await open();
  await svg().waitFor();
  assert.strictEqual(await status().textContent(), "2 tables · 1 from keys · 0 guessed from names",
    "regions has neither region_id nor id, so nothing is guessed");
  assert.strictEqual(await svg().locator("path title").textContent(),
    "hr.employees.manager_id → hr.employees.employee_id (from the warehouse's keys)");
  assert.strictEqual(await svg().getByText("+ 3 more columns").count(), 1);
  assert.strictEqual(await svg().locator("rect").first().getAttribute("fill"), "#ffffff");
});

test("Fit shows the diagram at full size and back; dark mode draws it dark", { skip }, async () => {
  await page().evaluate(() => { document.documentElement.dataset.colorMode = "dark"; });
  await open();
  await svg().waitFor();
  assert.strictEqual(await svg().locator("rect").first().getAttribute("fill"), "#1d2128");
  assert.strictEqual(await svg().getAttribute("width"), null, "fitted to the window");
  const fit = dialog().getByRole("button", { name: "Fit" });
  await fit.click();
  assert.strictEqual(await fit.getAttribute("aria-pressed"), "false");
  assert.match(await svg().getAttribute("width"), /^\d+$/);
  await fit.click();
  assert.strictEqual(await fit.getAttribute("aria-pressed"), "true");
  assert.strictEqual(await svg().getAttribute("width"), null);
});

test("other schemas: too many tables, none, and tables that can't be read", { skip }, async () => {
  await open();
  await svg().waitFor();
  const pick = dialog().getByLabel("Schema");
  await pick.selectOption("wide");
  await page().waitForFunction(() => /Showing the first 40\./.test(document.querySelector("#dialog-root [role=status]").textContent));
  assert.strictEqual(await status().textContent(), "40 of 45 tables · 0 from keys · 0 guessed from names. Showing the first 40.");
  await pick.selectOption("empty");
  await dialog().getByText("empty has no tables.").waitFor();
  assert.strictEqual(await svg().count(), 0);
  await pick.selectOption("broken");
  await dialog().getByRole("alert").filter({ hasText: /^Couldn't read the tables: / }).waitFor();
  assert.strictEqual(await status().textContent(), "");
});

test("Enter on a table closes the diagram and opens the table in the Explorer", { skip }, async () => {
  await open();
  await svg().waitFor();
  await svg().getByRole("button", { name: "regions: open it in the Explorer" }).focus();
  await page().keyboard.press("Enter");
  await page().waitForFunction(() => !document.querySelector("#dialog-root .dialog"));
  assert.deepStrictEqual(await page().evaluate(() => window.__revealed), [{ schema: "hr", name: "regions" }]);
});

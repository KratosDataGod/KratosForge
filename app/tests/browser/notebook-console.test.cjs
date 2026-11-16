// Notebook > View > Scratch (nbconsole.js, parity item 19, Preview): runs in the notebook's own session
// (start_cell with the notebook's name) without adding a cell or changing the notebook, keeps a history
// for Up, and Copy to a new cell adds the line at the end.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const ran = [];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }), notebook_syntax: () => [], kernel_variables: () => [],
    start_cell: ({ code, notebook }) => {
      ran.push({ code, notebook });
      if (code.includes("boom")) return { status: "error", outputs: [{ type: "error", ename: "NameError", evalue: "name 'boom' is not defined" }], version: 1 };
      return { status: "running", outputs: [], version: 1 };
    },
    poll_cell: () => ({ status: "ok", outputs: [{ type: "stream", text: "48210\n" }], version: 2 }),
  } });
  await page().evaluate(() => {
    loadIntoState({ name: "Weekly sales", cells: [{ type: "code", cell_id: "c1", source: "df = spark.read.table('gold.fact_sales')", meta: {} }] });
    go("notebook");
  });
  await page().locator(".cell .cm-content").first().waitFor();
});
after(async () => { await app?.close(); });

test("Scratch runs in the notebook's session, adds no cell, and keeps what ran for Up", { skip }, async () => {
  const p = page();
  await p.getByRole("tab", { name: "View" }).click();
  await p.getByRole("button", { name: /^Scratch/ }).click();
  const pane = p.getByRole("complementary", { name: "Scratch" });
  const box = pane.getByLabel("Scratch input");
  await box.waitFor();
  // Opening focuses the box on the next frame, so wait for it rather than checking at once.
  await p.waitForFunction(() => document.activeElement?.classList.contains("console-input"));
  await box.fill("df.count()");
  await box.press("Shift+Enter");
  await pane.getByText("48210").waitFor();
  assert.deepStrictEqual(ran.at(-1), { code: "df.count()", notebook: "Weekly sales" });
  assert.strictEqual(await box.inputValue(), "");
  assert.strictEqual(await p.evaluate(() => state.notebook.cells.length), 1, "no cell added");
  assert.strictEqual(await p.evaluate(() => !!state.notebook.dirty), false, "the notebook isn't changed");

  await box.fill("boom");
  await pane.getByRole("button", { name: "Run" }).click();
  await pane.getByText("NameError: name 'boom' is not defined").waitFor();
  await box.press("ArrowUp");
  assert.strictEqual(await box.inputValue(), "boom");
  await box.press("ArrowUp");
  assert.strictEqual(await box.inputValue(), "df.count()");
  await box.press("End");
  await box.press("ArrowDown");
  assert.strictEqual(await box.inputValue(), "boom");
  // Command-mode keys stay out of the box: typing "a" or "b" adds no cell.
  await box.fill("");
  await box.type("ab");
  assert.strictEqual(await p.evaluate(() => state.notebook.cells.length), 1);
  if (axeSource) assert.deepStrictEqual(await axeFindings(p, ".consolepane"), []);
});

test("Copy to a new cell adds the last line that ran at the end, and Clear empties the console", { skip }, async () => {
  const p = page();
  const pane = p.getByRole("complementary", { name: "Scratch" });
  await pane.getByLabel("Scratch input").fill("");
  await p.evaluate(() => { state.notebook.console.draft = ""; });
  await pane.getByRole("button", { name: "Copy to a new cell" }).click();
  await p.waitForFunction(() => state.notebook.cells.length === 2);
  assert.strictEqual(await p.evaluate(() => state.notebook.cells[1].source), "boom");
  await pane.getByRole("button", { name: "Clear" }).click();
  await pane.getByText("Type Python and press Shift+Enter", { exact: false }).waitFor();
  await pane.getByRole("button", { name: "Close Scratch" }).click();
  assert.strictEqual(await p.getByRole("complementary", { name: "Scratch" }).count(), 0);
});

test("while a cell runs, Run refuses and says to run again, and nothing is sent", { skip }, async () => {
  const p = page();
  await p.evaluate(() => { state.notebook.console.open = true; renderNotebook(); setKernel("busy", state.notebook); });
  const pane = p.getByRole("complementary", { name: "Scratch" });
  const before = ran.length;
  await pane.getByLabel("Scratch input").fill("df.count()");
  await pane.getByRole("button", { name: "Run" }).click();
  await p.getByText("A cell is running. Run this again when it finishes.").waitFor();
  assert.strictEqual(ran.length, before);
  assert.strictEqual(await pane.getByLabel("Scratch input").inputValue(), "df.count()", "what you typed stays");
  await p.evaluate(() => setKernel("idle", state.notebook));
});

test("on another engine the console says it runs on this PC's Python", { skip }, async () => {
  const p = page();
  await p.evaluate(() => { engineStates.set(state.notebook.name, { engine: "spark-local", status: "ready" }); renderNotebook(); });
  const pane = p.getByRole("complementary", { name: "Scratch" });
  await pane.getByText("The scratch console runs on this PC's Python.", { exact: false }).waitFor();
  assert.strictEqual(await pane.getByRole("button", { name: "Run" }).isDisabled(), true);
  await p.evaluate(() => { engineStates.delete(state.notebook.name); renderNotebook(); });
});

test("a notebook that runs in Fabric says the console isn't available there", { skip }, async () => {
  const p = page();
  await p.evaluate(() => { state.notebook.console.open = true; state.notebook.compute = "fabric"; renderNotebook(); });
  const pane = p.getByRole("complementary", { name: "Scratch" });
  await pane.getByText("This notebook runs in Fabric, so the scratch console isn't available.", { exact: false }).waitFor();
  assert.strictEqual(await pane.getByRole("button", { name: "Run" }).isDisabled(), true);
});

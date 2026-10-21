// Breakpoints and stepping (Prove stage 5, item 44) against the real engine: a breakpoint set from
// the line number gutter pauses the cell in a function, the panel shows that frame, Evaluate runs
// in it, the keys step, and Continue finishes the cell. No lakehouse is attached.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const SOURCE = 'x = 1\ndef double(n):\n    y = n * 2\n    return y\nz = double(x)\npassword = "s3cr3t-value"\nw = z + 1\nw';

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["start_cell", "poll_cell", "debug_cell", "interrupt_cell", "kernel_variables", "kernel_complete",
      "notebook_debug_get", "notebook_dependencies"],
    mocks: { save_notebook: () => ({ saved: true }) },
  });
  await page().evaluate((source) => {
    loadIntoState({ name: "Debug me", cells: [{ type: "code", cell_id: "c1", source, meta: {} }] });
    go("notebook");
  }, SOURCE);
});

after(async () => { if (app) await app.close(); });

test("a breakpoint from the gutter pauses in the function, and the panel steps and evaluates", { skip }, async () => {
  const p = page();
  await p.locator(".cm-lineNumbers .cm-gutterElement", { hasText: /^3$/ }).click();
  assert.deepStrictEqual(await p.evaluate(() => state.notebook.cells[0].breakpoints), [3]);
  await p.locator(".cm-line.cm-bp").waitFor();
  await p.evaluate(() => { runCell(state.notebook.cells[0]); });
  const panel = p.locator(".dbg-paused");
  await panel.getByText("Paused at line 3 (breakpoint)").waitFor({ timeout: 30000 });
  const text = await panel.innerText();
  assert.match(text, /Call stack: \(cell\) line 5 › double line 3/);
  assert.match(text, /Variables in double/);
  assert.match(await p.locator(".cm-line.cm-paused").innerText(), /y = n \* 2/);
  if (SHOTS) await p.screenshot({ path: path.join(SHOTS, "debugger-paused.png") });

  await panel.getByRole("textbox", { name: "Expression to evaluate" }).fill("n + 10");
  await panel.getByRole("button", { name: "Evaluate" }).click();
  await panel.locator(".dbg-eval", { hasText: "n + 10" }).getByText("11").waitFor();

  await p.keyboard.press("Shift+F11");
  await panel.getByText("Paused at line 6").waitFor();
  await p.keyboard.press("F10");
  await panel.getByText("Paused at line 7").waitFor();
  const vars = await panel.locator("table.vars").innerText();
  assert.match(vars, /password\s+str\s+12\s+hidden/);
  assert.ok(!(await panel.innerText()).includes("s3cr3t-value"));

  await panel.getByRole("button", { name: "Continue" }).click();
  await p.locator(".cell-status", { hasText: "Succeeded" }).waitFor({ timeout: 30000 });
  assert.strictEqual(await p.locator(".dbg-paused").count(), 0);
  assert.strictEqual(await p.locator(".cm-line.cm-paused").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Debug cell pauses at the first line, and Stop cancels it keeping variables", { skip }, async () => {
  const p = page();
  await p.evaluate(() => { debugCell(state.notebook.cells[0]); });
  const panel = p.locator(".dbg-paused");
  await panel.getByText("Paused at line 1").waitFor({ timeout: 30000 });
  await panel.getByRole("button", { name: "Stop" }).click();
  await p.locator(".cell-status", { hasText: "Cancelled" }).waitFor({ timeout: 30000 });
  assert.match(await p.locator(".cell-results").innerText(), /Variables are kept/);
  assert.strictEqual(await p.locator(".dbg-paused").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

// ipywidgets (widgets.py, ipywidgets.js): an interact cell draws its controls, and changing one runs the
// function again in the real engine. No lakehouse is attached, so nothing reaches OneLake.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [{ type: "code", cell_id: "c1", meta: {},
  source: "from ipywidgets import interact\n@interact(n=(1, 10), name=['East', 'West'], loud=False)\n" +
    "def report(n, name, loud):\n    text = f'{name} x{n}'\n    print(text.upper() if loud else text)\n" }];

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 800 },
    engine: ["start_cell", "poll_cell", "interrupt_cell", "kernel_variables", "notebook_ipywidget_event"],
    mocks: { save_notebook: () => ({ saved: true }), notebook_dependencies: () => ({ cells: [] }) },
  });
  await page().evaluate((cells) => { loadIntoState({ name: "Widgets test", cells }); go("notebook"); }, CELLS);
  await page().evaluate(() => runCell(state.notebook.cells[0]));
  await page().locator(".widget-output").waitFor({ timeout: 60000 });
});

after(async () => { if (app) await app.close(); });

test("interact's controls rerun the function in the engine", { skip }, async () => {
  const out = page().locator(".widget-output");
  await out.getByText("East x5").waitFor();
  await out.locator('input[type="range"]').evaluate((el) => { el.value = "9"; el.dispatchEvent(new Event("change")); });
  await out.getByText("East x9").waitFor();
  await page().locator(".widget-output select").selectOption({ label: "West" });
  await page().locator(".widget-output").getByText("West x9").waitFor();
  await page().locator(".widget-output").getByRole("checkbox", { name: "loud" }).check();
  await page().locator(".widget-output").getByText("WEST X9").waitFor();
  assert.strictEqual(await page().locator(".widget-output .widget-readout").innerText(), "9");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "ipywidgets.png") });
  assert.deepStrictEqual(app.errors, []);
});

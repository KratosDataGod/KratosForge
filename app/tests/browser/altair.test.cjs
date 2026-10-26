// Altair charts (chart_output.py, renderVegaLite in notebook.js): a cell's chart draws with the bundled
// Vega libraries inside the sandboxed output frame, under the shell's own frame policy, so nothing is
// fetched. The frame page and its policy are read from main.rs; no lakehouse is attached.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const main = fs.readFileSync(path.join(__dirname, "..", "..", "src-tauri", "src", "main.rs"), "utf8");
const FRAME = main.match(/const OUTPUT_FRAME: &str = r#"([\s\S]*?)"#;/)[1];
// The shell's policy, less frame-ancestors (this test's page is a file, not the app's origin).
const CSP = main.match(/const OUTPUT_CSP: &str = "([\s\S]*?)";/)[1].replace(/\\\s+/g, "").replace(/frame-ancestors[^;]*$/, "");

const CELLS = [{ type: "code", cell_id: "c1", meta: {},
  source: "import altair as alt, pandas as pd\nsales = pd.DataFrame({'region': ['East', 'West', 'North'], 'amount': [5, 3, 8]})\n" +
    "alt.Chart(sales).mark_bar().encode(x='region', y='amount')" }];

let app;
const page = () => app.page;
const fetched = [];

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 800 },
    engine: ["start_cell", "poll_cell", "interrupt_cell", "kernel_variables"],
    mocks: { save_notebook: () => ({ saved: true }), notebook_dependencies: () => ({ cells: [] }) },
  });
  await page().route("http://nbout.test/**", (route) => route.fulfill({
    contentType: "text/html; charset=utf-8", headers: { "Content-Security-Policy": CSP }, body: FRAME }));
  page().on("request", (r) => { if (/^https?:/.test(r.url()) && !r.url().startsWith("http://nbout.test/")) fetched.push(r.url()); });
  await page().evaluate((cells) => {
    window.__TAURI__.core.convertFileSrc = () => "http://nbout.test/frame";
    loadIntoState({ name: "Altair test", cells }); go("notebook");
  }, CELLS);
  await page().evaluate(() => runCell(state.notebook.cells[0]));
});

after(async () => { if (app) await app.close(); });

test("an Altair chart draws in the cell's frame with nothing fetched", { skip }, async () => {
  await page().getByText("Altair chart").waitFor({ timeout: 60000 });
  const chart = page().frameLocator("iframe.out-html").locator("svg .mark-rect path");
  await chart.first().waitFor({ timeout: 30000 });
  assert.strictEqual(await chart.count(), 3);
  const labels = await page().frameLocator("iframe.out-html").locator("svg text").allTextContents();
  for (const region of ["East", "West", "North"]) assert.ok(labels.includes(region), `${region} labels the axis`);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "altair.png") });
  assert.deepStrictEqual(fetched, []);
  // The harness's start-up script runs in every frame, and the sandboxed one has no localStorage.
  assert.deepStrictEqual(app.errors.filter((e) => !/localStorage.*sandboxed/.test(e)), []);
});

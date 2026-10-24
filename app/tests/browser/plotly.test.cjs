// plotly figures (chart_output.py, renderPlotly in notebook.js): a cell's figure arrives as its JSON and
// draws with plotly.js, read once from the engine, inside the sandboxed output frame under the shell's own
// frame policy, so nothing is fetched. The frame page and its policy are read from main.rs; no lakehouse
// is attached.
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

const CELLS = [
  { type: "code", cell_id: "c1", meta: {},
    source: "import plotly.graph_objects as go\ngo.Figure(go.Bar(x=['East', 'West', 'North'], y=[5, 3, 8]))" },
  { type: "code", cell_id: "c2", meta: {},
    source: "import plotly.express as px\npx.line(x=[1, 2, 3], y=[2, 1, 3]).show()" },
];

let app;
const page = () => app.page;
const fetched = [];

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 800 },
    engine: ["start_cell", "poll_cell", "interrupt_cell", "kernel_variables", "notebook_plotly_js"],
    mocks: { save_notebook: () => ({ saved: true }), notebook_dependencies: () => ({ cells: [] }) },
  });
  await page().route("http://nbout.test/**", (route) => route.fulfill({
    contentType: "text/html; charset=utf-8", headers: { "Content-Security-Policy": CSP }, body: FRAME }));
  page().on("request", (r) => { if (/^https?:/.test(r.url()) && !r.url().startsWith("http://nbout.test/")) fetched.push(r.url()); });
  await page().evaluate((cells) => {
    window.__TAURI__.core.convertFileSrc = () => "http://nbout.test/frame";
    loadIntoState({ name: "plotly test", cells }); go("notebook");
  }, CELLS);
  await page().evaluate(async () => { for (const c of state.notebook.cells) await runCell(c); });
});

after(async () => { if (app) await app.close(); });

test("plotly figures draw in their cells' frames, with plotly.js read once and nothing fetched", { skip }, async () => {
  const frames = page().locator("iframe.out-html");
  await frames.nth(1).waitFor({ timeout: 60000 });
  const bars = frames.nth(0).contentFrame().locator(".barlayer .point path");
  await bars.first().waitFor({ timeout: 30000 });
  assert.strictEqual(await bars.count(), 3);
  const labels = await frames.nth(0).contentFrame().locator(".xtick text").allTextContents();
  assert.deepStrictEqual(labels, ["East", "West", "North"]);
  // fig.show() draws in its cell too.
  await frames.nth(1).contentFrame().locator(".scatterlayer .js-line").waitFor({ timeout: 30000 });
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "plotly.png") });
  assert.strictEqual(app.calls.filter((c) => c.method === "notebook_plotly_js").length, 1, "plotly.js was read more than once");
  // The outputs hold the figures, not plotly.js.
  const sizes = await page().evaluate(() => state.notebook.cells.map((c) => JSON.stringify(c.outputs).length));
  assert.ok(sizes.every((n) => n < 20000), `an output carries plotly.js: ${sizes}`);
  assert.deepStrictEqual(fetched, []);
  // The harness's start-up script runs in every frame, and the sandboxed one has no localStorage.
  assert.deepStrictEqual(app.errors.filter((e) => !/localStorage.*sandboxed/.test(e)), []);
});

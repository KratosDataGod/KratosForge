// Notebook Export > PDF (notebook-formats.js): the real engine makes the web page export, the app
// shows it alone for printing and calls the system's print dialog, and the page goes away after.
// The print dialog itself is the system's, so here print() is recorded and the print layout is
// checked by printing the page to a PDF with Chromium (what "Save as PDF" does).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "markdown", cell_id: "m1", source: "# Daily sales\nTotals per region.", meta: {} },
  { type: "code", cell_id: "c1", source: "total = 41 + 1\nprint(total)", meta: {} },
  { type: "code", cell_id: "c2", source: "display(df)", meta: {} },
];

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["notebook_print_page"], mocks: { save_notebook: () => ({ saved: true }) } });
  await page().evaluate((cells) => {
    loadIntoState({ name: "Daily sales", cells });
    window.printed = 0;
    window.print = () => { window.printed += 1; };
    go("notebook");
    window.__TAURI__.core.convertFileSrc = () => "about:blank";  // the shell's output frame, for the HTML output
    const [, c1, c2] = state.notebook.cells;
    c1.outputs = [{ type: "stream", text: "42\n" }];
    c1.count = 1;
    c2.outputs = [{ type: "table", columns: ["region", "total"], rows: [["east", 10], ["<b>west</b>", 32]], total_rows: 2 },
      { type: "html", html: "<script>parent.hacked = 1</script>" }];
    c2.count = 2;
  }, CELLS);
});

after(async () => { if (app) await app.close(); });

test("Export > PDF prints the notebook's page alone, escaped, and removes it after", { skip }, async () => {
  await page().evaluate(() => nbfExport("pdf"));
  await page().waitForFunction(() => window.printed === 1);
  await page().locator("#toast").getByText(/Save as PDF or Microsoft Print to PDF/).waitFor();
  const root = page().locator("#nbf-print");
  assert.strictEqual(await root.isVisible(), false);  // nothing changes on screen
  assert.match(await root.locator("h1").textContent(), /Daily sales/);
  assert.strictEqual(await root.locator(".nbfp-cell").count(), 3);
  assert.strictEqual(await root.locator("td", { hasText: "<b>west</b>" }).count(), 1);  // shown as text
  assert.strictEqual(await root.locator("script, style, b").count(), 0);
  assert.match(await root.textContent(), /HTML output isn't included/);
  assert.strictEqual(await page().evaluate(() => window.hacked), undefined);

  await page().emulateMedia({ media: "print" });
  const pr = await page().evaluate(() => ({
    root: getComputedStyle(document.getElementById("nbf-print")).display,
    shell: [...document.body.children].filter((el) => el.id !== "nbf-print" && getComputedStyle(el).display !== "none").length,
  }));
  assert.deepStrictEqual(pr, { root: "block", shell: 0 });
  if (SHOTS) {
    await page().screenshot({ path: path.join(SHOTS, "notebook-pdf-print-layout.png"), fullPage: true });
    await page().pdf({ path: path.join(SHOTS, "notebook-pdf.pdf"), format: "A4" });
  }
  await page().emulateMedia({ media: "screen" });

  await page().evaluate(() => window.dispatchEvent(new Event("afterprint")));
  assert.strictEqual(await root.count(), 0);
  assert.strictEqual(await page().evaluate(() => document.body.classList.contains("nbf-printing")), false);
  assert.deepStrictEqual(app.errors, []);
});

test("the PDF item is in the Export menu", { skip }, async () => {
  const labels = await page().evaluate(() => nbfExportMenu().map((x) => x.label).filter(Boolean));
  assert.ok(labels.includes("PDF (print to PDF)"));
});

// The report's window (nbreport.js) beyond notebook-report.test.cjs: a report the engine can't give
// says so and how to open it again; once it can, the report shows, and Export as PDF prints the same
// page. notebook_report_page is mocked; window.print is replaced so no print dialog opens.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const REPORT = { name: "Month-end close", at: "2026-11-01T09:00:00Z", stale: false,
  html: '<h1 class="title">Month-end close</h1><p class="md">Ledger balanced on <b>1 November</b>.</p><script>bad()</script>' };
let app;
let ready = false;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ search: "?popout=report", mocks: {
    notebook_report_page: () => {
      if (!ready) throw new Error("The report isn't open any more: the engine restarted.");
      return REPORT;
    },
  } });
});
after(async () => { await app?.close(); });

test("a report the engine can't give says why and how to open it again", { skip }, async () => {
  ready = false;
  await page().evaluate(() => dispatchEvent(new Event("focus")));  // read again, whatever an earlier test showed
  const alert = page().getByRole("alert");
  await alert.waitFor();
  assert.match(await alert.textContent(), /^Couldn't read the report: .* Open it again from the notebook \(Share > Open as report\)\.$/);
  assert.strictEqual(await page().getByRole("article").count(), 0);
});

test("once the engine has it the report shows, and Export as PDF prints the same page", { skip }, async () => {
  ready = true;
  await page().evaluate(() => { window.__printed = 0; window.print = () => { window.__printed += 1; }; dispatchEvent(new Event("focus")); });
  const report = page().getByRole("article", { name: "Month-end close, report" });
  await report.getByText("Ledger balanced on").waitFor();
  assert.strictEqual(await page().getByRole("alert").count(), 0);

  const from = app.calls.length;
  await page().getByRole("button", { name: "Export as PDF" }).click();
  await page().waitForFunction(() => window.__printed === 1);
  assert.strictEqual(await page().locator("#toast").textContent(), "In the print window, pick Save as PDF or Microsoft Print to PDF as the printer.");
  const printed = page().locator("#nbf-print");
  assert.strictEqual(await printed.locator("h1.nbfp-title").textContent(), "Month-end close");
  assert.strictEqual(await printed.locator("script").count(), 0, "the page is printed without its scripts");
  await page().evaluate(() => dispatchEvent(new Event("afterprint")));
  assert.strictEqual(await printed.count(), 0);
  assert.deepStrictEqual(app.calls.slice(from).map((c) => c.method), [],
    "printing reads nothing more from the engine");
});

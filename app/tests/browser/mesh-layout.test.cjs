// Data mesh: every tab has the same layout (meta.js meshPage). The header holds that tab's buttons with one
// primary, last; the view row holds Refresh; no card repeats the tab's name; nothing overflows at 1280 px.
// Runs against the real engine with a sample project (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ engine: { length: 1, includes: (m) => m !== "sign_in" } });
  await page().setViewportSize({ width: 1280, height: 800 });
  const dir = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "fd-mesh-layout-")), "proj");
  await page().evaluate((p) => rpc("meta_init", { path: p, preset: "3", sample: true }), dir);
});
after(async () => { if (app) await app.close(); });

// [tab, label on the tab row, the header's buttons in order]
const TABS = [
  ["home", "Home", ["Ask for data"]],
  ["catalog", "Products", ["Ask for data"]],
  ["requests", "Requests", ["New request"]],
  ["domains", "Domains", ["Glossary", "New domain", "Import from Fabric"]],
  ["entities", "Project", ["New entity", "Schedule", "Run all"]],
];

for (const [tab, label, buttons] of TABS) {
  test(`${label}: the header holds the tab's buttons, the main one last; the view row has Refresh`, { skip }, async () => {
    await page().evaluate((t) => goMeta(t), tab);
    await page().waitForFunction((n) => document.querySelectorAll(".mesh-page .page-head .head-actions button").length >= n
      && !document.querySelector(".meta-body .loading"), buttons.length);
    const head = await page().$$eval(".mesh-page .page-head .head-actions button", (b) => b.map((x) => ({
      text: x.textContent.replace("Preview", "").trim(), primary: x.classList.contains("primary") })));
    assert.deepStrictEqual(head.map((b) => b.text), buttons);
    assert.deepStrictEqual(head.map((b) => b.primary), buttons.map((_, i) => i === buttons.length - 1), "one primary, last");
    assert.ok(await page().$('.mesh-view-row button:has-text("Refresh")'));
    const titles = await page().$$eval(".meta-body h2", (h) => h.map((x) => x.textContent.trim()));
    assert.ok(!titles.includes(label) && !titles.includes(`Data ${label.toLowerCase()}`), `no card repeats the tab: ${titles}`);
    const overflow = await page().$eval(".mesh-view-row", (r) => r.scrollWidth > r.clientWidth + 1);
    assert.strictEqual(overflow, false, "the view row wraps rather than overflows at 1280 px");
    assert.deepStrictEqual(app.errors, []);
  });
}

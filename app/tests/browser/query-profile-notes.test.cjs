// Learn from execution (GD-04): notes under a notebook result's Query profile (qprofile.js), from what
// the profile measured (learn/evidence.py, tested in test_learn_evidence.py with recorded profiles).
// Here: the notes show with their measured line and rule link, the principle by guidance level, and a
// profile without notes shows nothing extra.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const STEPS = [
  { depth: 0, name: "HASH_GROUP_BY", rows: 7, seconds: 0.012, share: 0.16, details: {} },
  { depth: 1, name: "SEQ_SCAN", rows: 369600, rows_scanned: 3000000, seconds: 0.064, share: 0.84,
    details: { Table: "memory.main.sales", Filters: '("year"(order_date) = 2026)' } },
];
const NOTE = { rule_id: "SQ002", step: 1, url: "sql-best-practices.md#sq002",
  text: "The scan of sales read 3,000,000 rows and kept 369,600 (12%), 84% of the query's time. Its filter wraps order_date in year(), so the engine couldn't skip data by order_date's values.",
  principle: "Compare the column itself with a value or a range. Put any function on the value's side, so the engine can skip the data it doesn't need.",
  measured: {} };
let notes = [NOTE];

let app;
const page = () => app.page;
const box = () => page().locator("#gd04 .qprofile");

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "save_app_settings", "learn_catalog"],
    mocks: { notebook_frame_query_profile: () => ({ seconds: 0.076, rows: 7, rows_scanned: 3000000, peak_memory: 1048576,
      more_steps: 0, steps: STEPS, notes }) },
  });
  await page().waitForFunction(() => state.settings && "learn_mode" in state.settings);
});

after(async () => { await app?.close(); });

async function profile() {
  await page().evaluate(() => {
    document.getElementById("gd04")?.remove();
    const host = h("div", { id: "gd04", class: "card pad" }, renderQueryProfile({ frame: "f1" }));
    document.querySelector("main, #app, body").prepend(host);
  });
  await box().getByRole("button", { name: "Profile the query" }).click();
  await box().locator(".qprofile-stats").waitFor();
}

async function setMode(mode) {
  await page().evaluate((mode) => saveSettings({ learn_mode: mode }), mode);
  await page().waitForFunction((mode) => state.settings.learn_mode?.value === mode, mode);
}

test("a note says what was measured, links its rule, and Professional adds the principle", { skip }, async () => {
  await profile();
  const note = box().locator(".qprofile-note[data-rule=SQ002]");
  await note.waitFor();
  const said = await box().locator(".qprofile-notes").innerText();
  assert.match(said, /^What this profile shows\s+Measured\s+The scan of sales read 3,000,000 rows and kept 369,600 \(12%\), 84% of the query's time/);
  assert.match(said, /The principle: Compare the column itself/);
  assert.match(said, /Measured in this run on this PC\. Fabric's Spark may plan the query differently\./);
  await note.getByRole("link", { name: "About SQ002" }).click();
  const opened = await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_doc").map((c) => c.args.page));
  assert.deepStrictEqual(opened.slice(-1), ["sql-best-practices.md#sq002"]);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "query-profile-notes.png") });
});

test("Expert keeps the measured line and the rule, without the principle", { skip }, async () => {
  await setMode("expert");
  await profile();
  await box().locator(".qprofile-note").waitFor();
  assert.doesNotMatch(await box().locator(".qprofile-notes").innerText(), /The principle/);
  await setMode("professional");
});

test("Notebook guidance sets the level here, whatever the overall level", { skip }, async () => {
  await page().evaluate(() => saveSettings({ learn_mode: "professional", learn_mode_notebooks: "expert" }));
  await page().waitForFunction(() => state.settings.learn_mode_notebooks?.value === "expert");
  await profile();
  await box().locator(".qprofile-note").waitFor();
  assert.doesNotMatch(await box().locator(".qprofile-notes").innerText(), /The principle/);
  await page().evaluate(() => saveSettings({ learn_mode_notebooks: "same" }));
  await page().waitForFunction(() => state.settings.learn_mode_notebooks?.value === "same");
});

test("a profile with no notes shows nothing extra", { skip }, async () => {
  notes = [];
  await profile();
  assert.strictEqual(await box().locator(".qprofile-notes").count(), 0);
  assert.doesNotMatch(await box().innerText(), /\bnull\b/, "nothing left out is written as null");
  notes = [NOTE];
});

test("with preview features hidden, no notes", { skip }, async () => {
  await page().evaluate(() => saveSettings({ show_preview: false }));
  await page().waitForFunction(() => !previewShown());
  await profile();
  assert.strictEqual(await box().locator(".qprofile-notes").count(), 0);
  await page().evaluate(() => saveSettings({ show_preview: null }));
});

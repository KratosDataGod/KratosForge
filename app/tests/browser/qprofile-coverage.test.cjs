// The Query profile view (qprofile.js), the paths output-views, sql-editor and query-profile-notes leave
// out: a profile that fails and is asked again, a slow query in tenths of a second, a step DuckDB names
// that the app doesn't know, a long detail cut short, steps that aren't listed, Copy as text with the
// whole profile, Run again that fails over a profile already shown, and a result whose kernel restarted.
// The profile is fetched through the view's own fetch hook with replies shaped like
// spark_local.query_profile's; what the person sees and what is copied is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const LONG = "o_orderdate >= '2026-01-01' AND o_orderstatus = 'F' AND o_totalprice > 1000";
const PROFILE = { seconds: 12.345, rows_scanned: 1500000, rows: 42, peak_memory: 64 * 1024 * 1024, more_steps: 3, notes: [], steps: [
  { name: "HASH_GROUP_BY", depth: 0, rows: 42, seconds: 0.5, share: 0.04, details: { Groups: "#0" } },
  { name: "FILTER", depth: 1, rows: 90000, seconds: 1.2, share: 0.1, details: { Expression: LONG } },
  { name: "MY_CUSTOM_SCAN ", depth: 2, rows: 1500000, seconds: 10.6, share: 0.86, details: {} }] };

let app;
const page = () => app.page;
const box = () => page().locator("#t-box");

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {} });
  await page().evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async (t) => { window.__copied = t; } }, configurable: true });
  });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => {
    window.__copied = null; window.__answers = [];
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
  });
  app.errors.length = 0;
});

// Draws the view with a fetch that answers from window.__answers in turn: a profile, or {fail: text}.
const mount = () => page().evaluate(() => {
  let b = document.getElementById("t-box");
  if (!b) { b = document.createElement("div"); b.id = "t-box"; document.body.append(b); }
  window.__fetched = 0;
  const fetch = () => { window.__fetched++; const a = window.__answers.shift(); return a.fail ? Promise.reject(new Error(a.fail)) : Promise.resolve(a); };
  b.replaceChildren(renderQueryProfile({ fetch }));
});
const answer = (...a) => page().evaluate((a) => { window.__answers.push(...a); }, a);

test("a profile that fails says why; asked again it shows each step, the slowest, and the steps left out", { skip }, async () => {
  await answer({ fail: "RuntimeError: The table can't be read from OneLake" }, PROFILE);
  await mount();
  await box().getByRole("button", { name: "Profile the query" }).click();
  await box().getByRole("alert").waitFor();
  assert.match(await box().getByRole("alert").innerText(), /The table can't be read from OneLake/);
  await box().getByRole("button", { name: "Profile the query" }).click();
  await box().locator("tbody tr").first().waitFor();
  assert.strictEqual(await box().getByRole("alert").count(), 0);
  assert.deepStrictEqual(await box().locator(".qprofile-stat").allInnerTexts().then((t) => t.map((x) => x.replace(/\s+/g, " "))),
    ["Time 12.3 s", "Rows read 1,500,000", "Rows returned 42", "Peak memory 64.0 MB"]);
  const rows = await box().locator("tbody tr").allInnerTexts();
  assert.match(rows[0], /^Group by · #0\s+42\s+0\.5 s\s+4%$/);
  assert.match(rows[1], /^Filter · o_orderdate >= '2026-01-01' AND o_orderstatus = 'F' AND o_t…\s+90,000/);
  assert.match(rows[2], /^MY_CUSTOM_SCANSlowest step\s+1,500,000\s+10\.6 s\s+86%$/);
  assert.strictEqual(await box().locator("tr.slowest").count(), 1);
  assert.strictEqual(await box().locator("tbody tr").nth(1).locator("td").first().getAttribute("title"), `Expression: ${LONG}`);
  assert.match(await box().locator(".more").last().innerText(), /^3 more steps aren't listed\. Steps are DuckDB's/);
  assert.strictEqual(await page().evaluate(() => window.__fetched), 2);
  assert.deepStrictEqual(app.errors, []);
});

test("Copy as text copies the whole profile; Run again that fails keeps the profile and says why", { skip }, async () => {
  await answer(PROFILE, { fail: "RuntimeError: DuckDB ran out of memory" });
  await mount();
  await box().getByRole("button", { name: "Profile the query" }).click();
  await box().getByRole("button", { name: "Copy as text" }).click();
  await page().waitForFunction(() => window.__copied);
  assert.strictEqual(await page().evaluate(() => window.__copied), [
    "Query profile: 12.3 s, 1,500,000 rows read, 42 rows returned, peak memory 64.0 MB",
    "Group by (#0): 42 rows, 0.5 s, 4%",
    "  Filter (o_orderdate >= '2026-01-01' AND o_orderstatus = 'F' AND o_t…): 90,000 rows, 1.2 s, 10%",
    "    MY_CUSTOM_SCAN: 1,500,000 rows, 10.6 s, 86%",
    "3 more steps not listed"].join("\n"));
  await page().waitForFunction(() => document.getElementById("toast").textContent === "Copied the query profile.");
  await box().getByRole("button", { name: "Run again" }).click();
  await box().getByRole("alert").waitFor();
  assert.match(await box().getByRole("alert").innerText(), /DuckDB ran out of memory/);
  assert.strictEqual(await box().locator("tbody tr").count(), 3, "the profile already shown stays");
  assert.deepStrictEqual(app.errors, []);
});

test("a result whose kernel restarted says to run the cell again", { skip }, async () => {
  await answer({ gone: true });
  await mount();
  await box().getByRole("button", { name: "Profile the query" }).click();
  await box().getByText("The kernel restarted since this result. Run the cell again to profile its query.").waitFor();
  assert.strictEqual(await box().getByRole("button").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

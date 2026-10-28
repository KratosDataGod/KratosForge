// PR-OPT-001b: the Optimize page, with the engine's Optimize calls mocked (see harness.cjs for how
// to run it). Cost and savings and Settings use the real engine, which needs no Fabric for them.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CONN = { workspace_id: "w1", workspace_name: "Platform - Prod", lakehouse_id: "lh1",
  lakehouse_name: "FabricDesktop_Optimization", schemas: true, schema_version: 1, access: "write" };
const FINDINGS = [
  { fingerprint: "fp1", rule_id: "small_files", artifact_key: "w1/i1", target: "dbo.fact_orders", severity: "high",
    title: "fact_orders has 1,240 small files", first_seen: "2026-10-01T00:00:00.000000+00:00",
    last_seen: "2026-10-18T00:00:00.000000+00:00", occurrences: 4, installations: 2 },
  { fingerprint: "fp2", rule_id: "vacuum", artifact_key: "w1/i2", target: "dbo.events", severity: "low",
    title: "events has 64 versions since its last vacuum", first_seen: "2026-10-18T00:00:00.000000+00:00",
    last_seen: "2026-10-18T00:00:00.000000+00:00", occurrences: 1, installations: 1 },
];

let app;
let status;
let recs;

before(async () => {
  if (skip) return;
  status = { allowed: true, default_name: "FabricDesktop_Optimization", installation_id: "3b1f6c2e-0000-4000-8000-000000009a40",
    label: "", active: null, connections: [] };
  recs = [{ recommendation_id: "r1", fingerprint: "fp1", kind: "optimize", state: "open", reason: "1,240 files under 8 MB",
    impact: "High", effort: "Low", confidence: "high", artifact_key: "w1/i1", events: 1 }];
  app = await openApp({
    engine: ["cost_report", "app_settings", "save_app_settings"],
    mocks: {
      optimize_status: () => status,
      optimize_findings: () => FINDINGS,
      optimize_recommendations: ({ include_closed }) => recs.filter((r) => include_closed || !["dismissed", "verified", "superseded"].includes(r.state)),
      optimize_set_recommendation: ({ recommendation_id, state, reason }) => {
        const r = recs.find((x) => x.recommendation_id === recommendation_id);
        Object.assign(r, { state, state_reason: reason || null, events: r.events + 1 });
        return r;
      },
      optimize_disconnect: () => { status = { ...status, active: null, connections: [] }; return { active: null }; },
    },
  });
  await app.page.waitForFunction(() => typeof renderOptimize === "function" && !!state.settings);
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const page = () => app.page;
const shot = async (name) => { if (SHOTS) await page().screenshot({ path: path.join(SHOTS, `${name}.png`) }); };

test("Optimize sits in the rail where Fabric was, and Fabric's portal is a Workspaces tab", { skip }, async () => {
  const rail = await page().locator("#rail-main .rail-item").allTextContents();
  assert.ok(rail.includes("Optimize"), rail.join(", "));
  assert.ok(!rail.includes("Fabric"), rail.join(", "));
  assert.ok(rail.length <= 8);
  await page().evaluate(() => go("workspaces"));
  assert.deepStrictEqual(await page().locator(".subtabs .tab").allTextContents(), ["Your workspaces", "Fabric portal"]);
});

test("before a lakehouse is connected, setup doesn't block Cost and savings", { skip }, async () => {
  await page().evaluate(() => { optimizePage.tab = "cost"; go("optimize"); });
  await page().waitForSelector(".optimize-setup");
  await page().waitForSelector(".cost-head");
  const text = await page().locator(".page.optimize").textContent();
  assert.match(text, /Results from this PC only · not shared with your team yet/);
  // The setup buttons are the page's own, top right beside the title, not a row above the tabs.
  const head = await page().$eval(".page.optimize > .page-head", (el) => {
    const title = el.querySelector("h1").getBoundingClientRect(), acts = el.querySelector(".head-actions").getBoundingClientRect();
    return { right: acts.left > title.right, top: acts.top < title.bottom, buttons: [...el.querySelectorAll(".head-actions button")].map((b) => b.textContent.trim()) };
  });
  assert.deepStrictEqual(head, { right: true, top: true, buttons: ["Connect to a lakehouse", "Create a lakehouse"] });
  assert.match(text, /Prices/);
  assert.ok(!/Monitoring hub/.test(text), "no Fabric Monitoring hub on Cost and savings");
  assert.ok(!app.calls.some((c) => c.method === "optimize_findings"), "nothing shared is read without a lakehouse");
  await shot("optimize-cost-not-connected");
});

test("connected, Overview lists open improvements and dismissing asks why", { skip }, async () => {
  status = { ...status, active: CONN, connections: [CONN] };
  await page().evaluate(() => { optimizePage.tab = "overview"; render(); });
  await page().waitForSelector(".optimize-body table.list");
  const text = await page().locator(".page.optimize").textContent();
  assert.match(text, /FabricDesktop_Optimization/);
  assert.match(text, /Read and write/);
  assert.match(text, /fact_orders has 1,240 small files/);
  await shot("optimize-overview");
  await page().locator("button", { hasText: "Dismiss..." }).click();
  await page().locator(".dialog input").fill("Table is retired next month");
  await page().locator(".dialog button", { hasText: /^Dismiss$/ }).click();
  await page().waitForFunction(() => !document.querySelector(".optimize-body table.list"));
  const call = app.calls.filter((c) => c.method === "optimize_set_recommendation").at(-1).params;
  assert.deepStrictEqual(call, { recommendation_id: "r1", state: "dismissed", reason: "Table is retired next month" });
  // Opportunities can show it again, and reopen it.
  await page().evaluate(() => { optimizePage.tab = "opportunities"; optimizePage.closed = true; render(); });
  await page().waitForSelector(".optimize-body table.list");
  assert.match(await page().locator(".optimize-body").textContent(), /Dismissed.*Table is retired next month/s);
  await shot("optimize-opportunities");
  await page().locator("button", { hasText: "Reopen" }).click();
  await page().waitForFunction(() => /Open/.test(document.querySelector(".optimize-body tbody")?.textContent || ""));
});

test("a read-only lakehouse shows results but offers no changes", { skip }, async () => {
  status = { ...status, active: { ...CONN, access: "read" }, connections: [{ ...CONN, access: "read" }] };
  await page().evaluate(() => { optimizePage.tab = "overview"; render(); });
  await page().waitForSelector(".optimize-body table.list");
  assert.match(await page().locator(".optimize-band").textContent(), /Read only/);
  assert.strictEqual(await page().locator(".optimize-body button", { hasText: /Accept|Dismiss|Mark applied/ }).count(), 0);
});

test("Settings lists the lakehouse on one line, and Disconnect asks first", { skip }, async () => {
  status = { ...status, active: CONN, connections: [CONN] };
  await page().evaluate(() => go("settings"));
  await page().waitForSelector("h2:has-text('Optimize lakehouses')");
  const section = page().locator("section", { has: page().locator("h2:has-text('Optimize lakehouses')") });
  assert.match(await section.textContent(), /FabricDesktop_Optimization.*Platform - Prod · Read and write · schema 1/s);
  await section.scrollIntoViewIfNeeded();
  await shot("optimize-settings");
  await section.locator("button", { hasText: "Disconnect..." }).click();
  assert.match(await page().locator(".dialog").textContent(), /Its data stays/);
  await page().locator(".dialog button", { hasText: "Cancel" }).click();
  assert.ok(!app.calls.some((c) => c.method === "optimize_disconnect"), "nothing happens without OK");
  assert.deepStrictEqual(app.errors, []);
});

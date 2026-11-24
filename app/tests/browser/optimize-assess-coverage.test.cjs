// Optimize > Assessment (optimize-assess.js), the paths optimize-assess.test (the engine's demonstration
// fixture) leaves out: a real workspace and a real capacity picked and assessed with the days chosen,
// Assess with nothing picked, an assessment the engine refuses, one that finds nothing, what couldn't be
// read (items, workspaces, run histories, workspaces left out), an opportunity sheet that opens its items
// in Fabric and says why it isn't counted, the keyboard on a row, and preview features turned off.
// optimization_assess is mocked with results shaped like sidecar opportunities/assess.py's.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const WS = "a1a1a1a1-0000-4000-8000-000000000001";
const WS2 = "a1a1a1a1-0000-4000-8000-000000000002";
const CAP = "c0c0c0c0-0000-4000-8000-0000000000ca";

const unknown = (reason) => ({ classification: "unknown", label: "Unknown", value: null, unit: "cu_hours", reason });
const estimated = (value, unit, more = {}) => ({ classification: "estimated", label: "Estimated", value, unit, method: "measured Fabric run time scaled to 30 days",
  assumptions: ["the next 30 days run like the measured period"], ...more });
const runs = (n, secs) => ({ read: true, days: 30, runs: n, seconds_total: secs });
function opp(id, title, more = {}) {
  return { id, rule: "OE-FULL", category: "full_refresh", category_label: "Full refresh", action: "improve", action_label: "Make incremental",
    title, artifacts: [{ workspace_id: WS, item_id: `${id}-item`, type: "DataPipeline", name: `pl_${id}`, role: "change" }],
    current: `pl_${id} copies the whole table every run.`, problem: "Rows that didn't change are copied again.",
    evidence: [{ text: "Copy activity with no watermark", source: "pipeline definition" }], recommendation: "Copy only rows changed since the last run.",
    validation: ["Compare row counts after one run"], rollback: "Set the copy back to the whole table.", alternatives: [],
    confidence: "high", effort: "low", risk: "low", counted: true, overlaps: [],
    impact: { basis: "run time", run_seconds_per_month: estimated(36000, "seconds"), cu_per_month: estimated(7200, "cu_seconds"), cu_value_per_month: unknown("No price"),
      performance: "Shorter runs" },
    next_step: { label: "Change the copy", items: [{ type: "DataPipeline", workspace_id: WS, item_id: `${id}-item`, name: `pl_${id}` }] }, ...more };
}
function result(more = {}) {
  const ops = [opp("orders", "pl_orders copies everything every run"),
    opp("orders2", "pl_orders also runs a second full copy", { counted: false, overlaps: ["orders"], confidence: "medium" }),
    opp("tidy", "Two notebooks share a helper", { counted: false, overlaps: [], impact: { basis: "maintenance", note: "Saves maintenance only." },
      next_step: { label: "Merge them", items: [] } })];
  return { scope: { kind: "workspace", workspace_id: WS }, fixture: false, days: 7,
    workspaces: [{ id: WS, name: "Sales", problem: null }, { id: WS2, name: "", problem: "403: you can't read its items" }],
    price: { price_per_cu_hour: 0.18, currency: "USD", source: "Settings > Cost" },
    totals: { workloads_affected: 1, run_seconds_per_month: estimated(36000, "seconds"), cu_per_month: estimated(2, "cu_hours"),
      cu_value_per_month: unknown("Part of the CU figure isn't known, so no value is given."), verified: unknown("Nothing has been implemented and measured yet."),
      realizable_reason: "Money is saved only when a capacity is resized, paused or retired." },
    counts: { opportunities: ops.length, counted: 1 },
    opportunities: ops, next_action: { opportunity_id: "orders", label: "Make pl_orders incremental" },
    coverage: { items: 5, analyzed: 3, usage_items: 0, by_type: { DataPipeline: 2, Notebook: 1 }, history_not_read: 2, not_analyzed_types: ["SemanticModel"],
      not_read: [{ name: "nb_secret", type: "Notebook", problem: "Its definition is encrypted." }] },
    workloads: [{ name: "pl_orders", type: "DataPipeline", definition: "read", runs: runs(7, 4200) },
      { name: "nb_secret", type: "Notebook", definition: "not_read", runs: { read: false } },
      { name: "sm_sales", type: "SemanticModel", definition: "unsupported", runs: runs(0, 0) }],
    ...more };
}

const db = {};
let app;
const page = () => app.page;
const view = () => page().locator(".assess-view");
const scope = () => page().locator(".assess-scope");
const sent = () => app.calls.filter((c) => c.method === "optimization_assess").map((c) => c.params);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    optimize_status: () => ({ allowed: true, active: null, connections: [], default_name: "FabricDesktop_Central" }),
    list_workspaces: () => [{ id: WS, displayName: "Sales", capacityId: CAP.toUpperCase() }, { id: WS2, displayName: "HR", capacityId: CAP },
      { id: "w3", displayName: "My workspace", capacityId: "" }],
    list_capacities: () => db.caps,
    optimization_assess: (p) => { if (db.assess instanceof Error) throw db.assess; return db.assess(p); },
  } });
  await page().waitForFunction(() => typeof optimizeAssessPanel === "function");
  await page().evaluate(() => { window.__opened = []; window.openLink = (u) => window.__opened.push(u); });
  app.errors.length = 0;
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  db.caps = [{ id: CAP, displayName: "Sales F64", sku: "F64" }];
  db.assess = () => result();
  await page().evaluate(() => {
    closeDialog(); closeDialog(); window.__opened = [];
    state.user = "chris@contoso.com"; state.workspaces = null;
    state.settings = { ...(state.settings || {}), show_preview: { value: true } };
    Object.assign(optimizeAssess, { scope: "workspace", workspaceId: "", capacityId: "", days: 30, result: null, running: false, error: "" });
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

async function openAssessment(ready = ".assess-empty") {
  await page().evaluate(() => { optimizeExcellence.view = "opportunities"; optimizePage.tab = "assessment"; go("optimize"); render(); });
  await page().locator(ready).waitFor();
}

test("Assess with nothing picked says to pick one; a workspace assessed over 7 days shows what couldn't be read", { skip }, async () => {
  await openAssessment();
  await scope().getByRole("combobox", { name: "Workspace" }).locator(`option[value="${WS}"]`).waitFor({ state: "attached" });
  await scope().getByRole("button", { name: "Assess" }).click();
  await page().waitForFunction(() => /^Pick a workspace first$/.test(document.getElementById("toast").textContent));
  assert.deepStrictEqual(sent(), []);
  await scope().getByRole("combobox", { name: "Workspace" }).selectOption(WS);
  await scope().getByRole("combobox", { name: "Run history" }).selectOption("7");
  await scope().getByRole("button", { name: "Assess" }).click();
  await view().locator(".assess-table").waitFor();
  assert.deepStrictEqual(sent(), [{ workspace_id: WS, days: 7 }]);
  assert.strictEqual(await scope().getByRole("button", { name: "Assess again" }).count(), 1);
  const cov = page().locator(".assess-cov");
  assert.match(await cov.innerText(), /5 items, 3 analyzed\s+2 Pipelines · 1 Notebook\s+1 couldn't be read\s+2 run histories couldn't be read/);
  const gaps = await cov.locator("details > ul li").allInnerTexts();
  assert.deepStrictEqual(gaps.slice(0, 3), ["nb_secret (Notebook): Its definition is encrypted. It isn't in the results, so they may be incomplete.",
    `${WS2}: 403: you can't read its items`, "Semantic models are listed but not analyzed yet."]);
  assert.strictEqual(await cov.locator("details").getAttribute("open"), "", "gaps are open when something wasn't read");
  const wl = await cov.locator("tbody tr").allInnerTexts();
  assert.match(wl[0], /pl_orders\s+Pipeline\s+Read\s+7\s+1\.2 h/);
  assert.match(wl[1], /nb_secret\s+Notebook\s+Not read\s+Not read\s+None/);
  assert.match(wl[2], /sm_sales\s+Semantic model\s+Not analyzed\s+0\s+None/);
  assert.match(await page().locator(".assess-next").innerText(), /pl_orders copies everything every run/);
  assert.match(await page().locator(".assess-next").innerText(), /10 h Estimated of Fabric run time a month/);
  assert.deepStrictEqual(app.errors, []);
});

test("an opportunity's sheet opens its items in Fabric; an uncounted one says why; Enter opens a row", { skip }, async () => {
  await page().evaluate((r) => { optimizeAssess.result = r; }, result());
  await openAssessment(".assess-table");
  const rows = view().locator(".assess-table tbody tr");
  assert.match(await rows.nth(1).innerText(), /Not counted: same workload as #1/);
  await rows.nth(1).focus();
  await page().keyboard.press("Enter");
  const sheet = page().getByRole("dialog", { name: "pl_orders also runs a second full copy" });
  await sheet.waitFor();
  assert.match(await sheet.innerText(), /Not counted in the totals because it touches the same workload as a higher-ranked opportunity\./);
  assert.match(await sheet.innerText(), /measured Fabric run time scaled to 30 days; the next 30 days run like the measured period/);
  await sheet.getByRole("button", { name: "Open pl_orders2 in Fabric" }).click();
  assert.strictEqual((await page().evaluate(() => window.__opened)).length, 1);
  await sheet.getByRole("button", { name: "Close" }).last().click();
  await sheet.waitFor({ state: "detached" });

  await rows.nth(2).click();
  const tidy = page().getByRole("dialog", { name: "Two notebooks share a helper" });
  assert.match(await tidy.innerText(), /What it's worth, per month \(maintenance\)\s+Saves maintenance only\./i);
  assert.strictEqual(await tidy.getByRole("button", { name: /in Fabric$/ }).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a capacity is picked from the workspaces on it, and an assessment that finds nothing says what to do", { skip }, async () => {
  db.assess = () => result({ scope: { kind: "capacity", capacity_id: CAP, workspaces_left_out: 4, budget: { reached: true }, note: "The read budget ran out." },
    opportunities: [], counts: { opportunities: 0, counted: 0 }, next_action: { opportunity_id: null, label: "Nothing to do: no opportunity was found." },
    workspaces: [{ id: WS, name: "Sales", problem: null }],
    coverage: { items: 1, analyzed: 1, usage_items: 1, by_type: { DataPipeline: 1 }, history_not_read: 0, not_analyzed_types: [], not_read: [] } });
  await openAssessment();
  await scope().getByRole("button", { name: "A capacity" }).click();
  const pick = scope().getByRole("combobox", { name: "Capacity" });
  await pick.locator(`option[value="${CAP.toLowerCase()}"]`).waitFor({ state: "attached" });
  assert.deepStrictEqual(await pick.locator("option").allInnerTexts(), ["Pick a capacity", "Sales F64 (F64) · 2 workspaces"]);
  await pick.selectOption(CAP.toLowerCase());
  await scope().getByRole("button", { name: "Assess" }).click();
  await page().locator(".assess-cov").waitFor();
  assert.deepStrictEqual(sent(), [{ capacity_id: CAP.toLowerCase(), days: 30 }]);
  assert.match(await page().locator(".assess-next").innerText(), /Nothing to do: no opportunity was found\./);
  assert.match(await page().locator(".assess-ops").innerText(), /0 found, 0 counted[\s\S]*Nothing to do: no opportunity was found\./);
  const cov = page().locator(".assess-cov");
  assert.match(await cov.innerText(), /Every definition was read\s+Every run history was read/);
  assert.strictEqual(await cov.locator("details").getAttribute("open"), null);
  const gaps = await cov.locator("details > ul li").allTextContents();
  assert.ok(gaps.includes("The read budget ran out."), gaps.join(" | "));
  assert.ok(gaps.includes("4 more workspaces on this capacity weren't read (25 at most)."), gaps.join(" | "));

  db.caps = [];
  await page().evaluate(() => { optimizeAssess.result = null; render(); });
  await pick.locator(`option[value="${CAP.toLowerCase()}"]`).waitFor({ state: "attached" });
  assert.strictEqual(await pick.locator(`option[value="${CAP.toLowerCase()}"]`).innerText(), `${CAP.toLowerCase()} · 2 workspaces`, "an unlisted capacity shows its id");
  assert.deepStrictEqual(app.errors, []);
});

test("an assessment the engine refuses says why; leaving the demonstration goes back to the start", { skip }, async () => {
  db.assess = new Error("ValueError: Pick a workspace: its Fabric id is needed.");
  await openAssessment();
  await scope().getByRole("combobox", { name: "Workspace" }).locator(`option[value="${WS}"]`).waitFor({ state: "attached" });
  await scope().getByRole("combobox", { name: "Workspace" }).selectOption(WS);
  await scope().getByRole("button", { name: "Assess" }).click();
  await view().getByRole("alert").waitFor();
  assert.match(await view().getByRole("alert").innerText(), /Pick a workspace: its Fabric id is needed\./);

  db.assess = () => result({ fixture: true, fixture_note: "Demonstration fixture: made-up runs." });
  await scope().getByRole("button", { name: "Try the demonstration fixture" }).click();
  await view().locator(".assess-fixture").waitFor();
  assert.deepStrictEqual(sent().at(-1), { demo: true });
  await scope().getByRole("button", { name: "Leave the demonstration" }).click();
  await view().locator(".assess-empty").waitFor();
  assert.strictEqual(await page().evaluate(() => optimizeAssess.result), null);
  assert.deepStrictEqual(app.errors, []);
});

test("signed out, the picker asks to sign in; with preview features off, the assessment says how to turn them on", { skip }, async () => {
  await page().evaluate(() => { state.user = null; });
  await openAssessment();
  assert.deepStrictEqual(await scope().getByRole("combobox", { name: "Workspace" }).locator("option").allInnerTexts(), ["Sign in to list workspaces"]);
  const box = await page().evaluate(() => {
    state.settings.show_preview = { value: false };
    const b = document.createElement("div");
    optimizeAssessPanel(b);
    return b.textContent;
  });
  assert.strictEqual(box, "The assessment is a Preview feature. Turn on preview features in Settings to use it.");
  assert.deepStrictEqual(app.errors, []);
});

// Optimize (optimize.js), the paths the optimize-* tests leave out: an organization that turned shared
// lakehouses off (the page and Settings say so), shared results that can't be read, Lakehouses going
// to Settings, more than five open improvements, Show dismissed and verified, no recommendations,
// Mark applied, Create and Connect with no workspace, Disconnect, this PC's own X-ray with nothing
// open or more than five, the palette's Optimize and Cost and savings, and Settings history: not
// connected, a scope with no changes, a read Fabric refuses, Set up history after a Cancel, and a
// read that found changes. The engine's replies are mocked; what the person sees and sends is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const CONN = { workspace_id: "w1", workspace_name: "Platform", lakehouse_id: "lh1", lakehouse_name: "FabricDesktop_Central", access: "write", schema_version: 1 };
const FINDINGS = [{ fingerprint: "fp1", target: "dbo.orders", severity: "high", title: "orders has 1,240 small files", occurrences: 2 }];
const rec = (i, state = "open") => ({ recommendation_id: `r${i}`, fingerprint: "fp1", kind: "optimize", state, reason: `reason ${i}`,
  impact: "High", effort: "Low", artifact_key: "w1/i1" });
const status = (more = {}) => ({ allowed: true, default_name: "FabricDesktop_Central", installation_id: "id-1", label: "",
  active: CONN, connections: [CONN], outbox: null, ...more });
const xfinding = (i) => ({ id: `x${i}`, kind: "small_files", severity: "medium", title: `t${i} has small files`, detail: "From the log.",
  workspace_id: "w1", workspace_name: "Sales", lakehouse_id: "lh9", lakehouse_name: "LH", table: `t${i}`, action: "optimize", effort: "Low", fixed: false, fix: null });
const CHANGES = [{ scope_type: "capacity", scope_id: "cap1", setting: "displayName", old_json: null, new_json: "\"F8 Prod\"",
  changed_after: null, changed_by_time: "2026-11-01T09:00:00Z" }];
const CONFIRM = "The settings history tables aren't in FabricDesktop_Central yet: it adds fdcap_snapshot, fdcap_change and fdcap_read. Confirm to continue.";

// Set before the app opens; each test changes what it needs.
const fresh = () => ({ status: status(), findingsFail: null, recs: [rec(1)], xray: { result: { scanned: "2026-11-01T08:00:00Z", findings: [] } },
  hist: { connected: true, changes: CHANGES, gaps: [], last_read: {} }, read: () => ({ outcome: "read", changes: [] }) });
const db = fresh();
let app;
const page = () => app.page;
const body = () => page().locator(".optimize-body");
const sent = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    optimize_status: () => db.status,
    optimize_findings: () => { if (db.findingsFail) throw new Error(db.findingsFail); return FINDINGS; },
    optimize_recommendations: ({ include_closed }) => db.recs.filter((r) => include_closed || !["dismissed", "verified"].includes(r.state)),
    optimize_set_recommendation: ({ recommendation_id, state }) => { db.recs.find((r) => r.recommendation_id === recommendation_id).state = state; return { ok: true }; },
    optimize_disconnect: () => { db.status = status({ active: null, connections: [] }); return { ok: true }; },
    xray: () => db.xray,
    tenant_settings_history: () => db.hist,
    capacity_settings_read: (p) => db.read(p),
  } });
  await page().evaluate(() => { state.settings = { ...(state.settings || {}), show_preview: { value: true } }; });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(db, fresh());
  await page().evaluate(() => {
    closeDialog(); closeDialog();
    Object.assign(optimizePage, { tab: "overview", closed: false, localDismissed: false });
    Object.assign(settingsHistoryPage, { scope: "all", overrides: false, busy: null });
    try { localStorage.removeItem("fd.settingsHistory.setup"); } catch { /* fine */ }
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    document.getElementById("t-box")?.remove();
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

const open = (tab) => page().evaluate((t) => { optimizePage.tab = t; go("optimize"); }, tab);
// Replaces a page function for the test, recording its calls.
const spy = (name) => page().evaluate((n) => { window.__spied = []; window[`__${n}`] = window[n]; window[n] = (...a) => { window.__spied.push(a); }; }, name);
const unspy = (name) => page().evaluate((n) => { window[n] = window[`__${n}`]; const s = window.__spied; window.__spied = []; return s; }, name);

test("an organization that turned shared lakehouses off: the page and Settings say so", { skip }, async () => {
  db.status = status({ allowed: false, active: null, connections: [] });
  await open("opportunities");
  await page().getByText("Shared optimization lakehouses are turned off by your organization. Cost and savings still shows this PC's figures.").waitFor();
  await body().getByText("Your organization has turned off shared optimization lakehouses.").waitFor();
  await page().evaluate(async () => { const b = document.createElement("div"); b.id = "t-box"; document.body.append(b); b.append(await optimizeSettingsSection()); });
  await page().locator("#t-box").getByText("Ask IT to change allow_optimize_lakehouse in Group Policy or the policy file.", { exact: false }).waitFor();
  assert.strictEqual(await page().locator("#t-box").getByRole("button").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("shared results that can't be read say why; Lakehouses opens Settings", { skip }, async () => {
  db.findingsFail = "The lakehouse's SQL endpoint isn't ready.";
  await open("overview");
  await body().getByText("The lakehouse's SQL endpoint isn't ready.", { exact: false }).waitFor();
  await spy("go");
  await page().getByRole("button", { name: "Lakehouses" }).click();
  assert.deepStrictEqual(await unspy("go"), [["settings"]]);
  assert.strictEqual(await page().evaluate(() => state.settingsTab), "general");
  assert.deepStrictEqual(app.errors, []);
});

test("more than five open improvements link to all of them; Show dismissed and verified asks for those too", { skip }, async () => {
  db.recs = [1, 2, 3, 4, 5, 6, 7].map((i) => rec(i)).concat([rec(8, "dismissed")]);
  await open("overview");
  await body().getByRole("button", { name: "All 7" }).click();
  await page().locator(".subtabs .tab.active", { hasText: "Opportunities" }).waitFor();
  await body().locator("tbody tr").nth(6).waitFor();
  assert.strictEqual(await body().locator("tbody tr").count(), 7);
  await body().getByRole("checkbox", { name: "Show dismissed and verified" }).check();
  await page().waitForFunction(() => document.querySelectorAll(".optimize-body tbody tr").length === 8);
  assert.strictEqual(sent("optimize_recommendations").at(-1).include_closed, true);
  assert.deepStrictEqual(app.errors, []);
});

test("Mark applied moves it on for everyone; with none left Opportunities says so", { skip }, async () => {
  db.recs = [rec(1)];
  await open("opportunities");
  await body().getByRole("button", { name: "Mark applied" }).click();
  await toastSays(/^Marked applied$/);
  assert.deepStrictEqual(sent("optimize_set_recommendation"), [{ recommendation_id: "r1", state: "applied", reason: "" }]);
  db.recs = [];
  await open("opportunities");
  await body().getByText("No recommendations yet.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Create and Connect with no workspace to pick say to sign in and open one", { skip }, async () => {
  db.status = status({ active: null, connections: [] });
  await page().evaluate(() => { window.__ws = state.workspaces; window.__user = state.user; state.user = "chris@contoso.com"; state.workspaces = []; });
  await page().evaluate(() => { optimizeCreateFlow(); });
  await toastSays(/^Sign in and open a workspace first$/);
  await page().evaluate(() => { const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; optimizeConnectFlow(); });
  await toastSays(/^Sign in and open a workspace first$/);
  await page().evaluate(() => { state.workspaces = window.__ws; state.user = window.__user; });
  assert.deepStrictEqual(sent("optimize_create"), []);
  assert.deepStrictEqual(app.errors, []);
});

test("Disconnect in Settings asks, then disconnects", { skip }, async () => {
  await page().evaluate(async () => { const b = document.createElement("div"); b.id = "t-box"; document.body.append(b); b.append(await optimizeSettingsSection()); });
  await spy("render");
  await page().locator("#t-box").getByRole("button", { name: "Disconnect..." }).click();
  await page().getByRole("dialog", { name: "Disconnect FabricDesktop_Central?" }).getByRole("button", { name: "Disconnect" }).click();
  await page().waitForFunction(() => window.__spied.length === 1);
  await unspy("render");
  assert.deepStrictEqual(sent("optimize_disconnect"), [{ lakehouse_id: "lh1" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("this PC's X-ray with nothing open says so; with more than five it links to Home", { skip }, async () => {
  db.status = status({ active: null, connections: [] });
  await page().evaluate(() => { window.__user = state.user; state.user = "chris@contoso.com"; });
  await open("overview");
  await body().getByText("Nothing needs attention in the last scan.").waitFor();
  db.xray = { result: { scanned: "2026-11-01T08:00:00Z", findings: [1, 2, 3, 4, 5, 6, 7].map(xfinding) } };
  await open("overview");
  await body().locator(".optimize-local tbody tr").nth(4).waitFor();
  assert.strictEqual(await body().locator(".optimize-local tbody tr").count(), 5);
  await spy("go");
  await body().getByRole("button", { name: "All 7 in the Estate X-ray on Home" }).click();
  assert.deepStrictEqual(await unspy("go"), [["home"]]);
  await page().evaluate(() => { state.user = window.__user; });
  assert.deepStrictEqual(app.errors, []);
});

test("the palette's Optimize and Cost and savings open those tabs", { skip }, async () => {
  await spy("go");
  await page().evaluate(() => { optimizePage.tab = "settings"; features.commands.find((c) => c.label === "Optimize").run(); });
  assert.strictEqual(await page().evaluate(() => optimizePage.tab), "overview");
  await page().evaluate(() => features.commands.find((c) => c.label === "Cost and savings").run());
  assert.strictEqual(await page().evaluate(() => optimizePage.tab), "cost");
  assert.deepStrictEqual(await unspy("go"), [["optimize"], ["optimize"]]);
  assert.deepStrictEqual(app.errors, []);
});

test("Settings history: not connected says how; a scope with no changes says so", { skip }, async () => {
  db.hist = { connected: false };
  await open("settings");
  await body().getByText("Connect this PC to a central lakehouse (Settings > Optimize lakehouses) to keep a settings history.").waitFor();
  db.hist = { connected: true, changes: CHANGES, gaps: [], last_read: {} };
  await open("settings");
  await body().locator(".sh-scope").selectOption("tenant");
  await body().getByText("No changes for this scope.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("a read Fabric refuses says why; Set up history after a Cancel creates the tables and reads", { skip }, async () => {
  db.read = () => { throw new Error("Capacity.Read.All isn't granted."); };
  await open("settings");
  await body().getByRole("button", { name: "Read capacities now" }).click();
  await toastSays(/^Capacities not read: Your sign-in can't list capacities/);
  db.read = (p) => {
    if (!p.approve) throw new Error(CONFIRM);
    return { outcome: "read", changes: [{}, {}] };
  };
  await body().getByRole("button", { name: "Read capacities now" }).click();
  const d = page().getByRole("dialog", { name: "Create the settings history tables?" });
  await d.getByRole("button", { name: "Cancel" }).click();
  const setup = body().locator(".sh-setup");
  await setup.getByText("it adds fdcap_snapshot", { exact: false }).waitFor();
  await setup.getByRole("button", { name: "Set up history" }).click();
  await toastSays(/^Capacities read: 2 changes$/);
  assert.deepStrictEqual(sent("capacity_settings_read").map((p) => !!p.approve), [false, false, true]);
  await body().locator(".sh-setup").waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

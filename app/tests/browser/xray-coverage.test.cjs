// The Estate X-ray card (xray.js), the paths the xray-* tests leave out: a scan that is still running
// and is read again by itself, Hide details and Show details, the guide links, tables it couldn't
// read, findings queued for or added to the central lakehouse, Show all and Show fewer, the Open in
// Fabric, Review access, Open notebook and See cost buttons, Clear on the pick bar, Check again in
// the Optimize and Vacuum dialogs, Vacuum all with a table it can't check, a batch Optimize whose
// check fails, and Adopt without a metadata project, with nothing picked, and with tables skipped.
// Fabric is mocked with xray_service.py's shapes; what the person sees and what was sent is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const where = { workspace_id: "w1", workspace_name: "Sales", lakehouse_id: "lh1", lakehouse_name: "LH_Silver" };
const fid = (f) => ["kind", "workspace_id", "lakehouse_id", "schema", "table", "person", "notebook"].map((k) => f[k] || "").join("|");
function finding(kind, table, severity, title, more = {}) {
  const action = { small_files: "optimize", vacuum: "vacuum", stale: "open", access: "access", cost: "cost" }[kind];
  const f = { ...where, schema: null, table, kind, severity, title, detail: `${title}.`, action, ...more };
  return { ...f, id: fid(f), fixed: false, fix: null };
}
const PASS = { id: "r-pass", result: "pass", label: "Pass", summary: "Nothing found that should stop this run", findings: [],
  can_proceed: true, needs_acknowledgement: false, enforced: false };
const WARNING = { id: "r-wrn", result: "warning", label: "Warning", summary: "1 thing(s) to check before you run",
  findings: [{ check: "capacity", outcome: "warning", label: "Warning", title: "The capacity F8 is 92% used", detail: "", evidence: [], remedy: "",
    source: "", observed_at: null, essential: false, measurement: null, can_override: true }],
  can_proceed: true, needs_acknowledgement: false, enforced: false };
function result(more = {}) {
  return {
    scanned: "2026-10-31T08:00:00+00:00", workspaces: 1, lakehouses: 2, tables: 9, partial: false, skipped: [], bytes: 5e9,
    findings: [
      finding("small_files", "orders", "high", "orders has 4,210 small files", { files: 4210 }),
      finding("small_files", "lines", "medium", "lines has 900 small files", { files: 900 }),
      finding("vacuum", "events", "low", "events has never been vacuumed"),
      finding("vacuum", "clicks", "low", "clicks has 31 versions since its last vacuum"),
      finding("stale", "old_sales", "low", "old_sales hasn't changed in 90 days"),
      finding("access", null, "medium", "3 people have Admin on Sales", { lakehouse_name: null, lakehouse_id: null, person: "x" }),
      finding("cost", null, "low", "Runs this app started used 40 CU hours", { lakehouse_name: null, lakehouse_id: null }),
      finding("cost", null, "medium", "daily_load runs in Fabric every hour", { lakehouse_name: null, lakehouse_id: null, notebook: "daily_load", action: "run_local" }),
    ],
    lakehouse_rows: [
      { ...where, tables: 5, bytes: 4e9, files: 6000, issues: 4, managed: false, adopted: 0 },
      { workspace_id: "w2", workspace_name: "Ops", lakehouse_id: "lh2", lakehouse_name: "LH_Gold", tables: 2, bytes: 1e9, files: 80, issues: 0, managed: true, adopted: 2 },
    ],
    ...more,
  };
}

const db = { scan: { running: false, paused: false, progress: null, error: null, result: result() }, fail: {}, project: null, risk: PASS };
const refuse = (m, p) => { const why = db.fail[m]; if (typeof why === "function" ? why(p) : why) throw new Error(typeof why === "function" ? why(p) : why); };
let app;
const page = () => app.page;
const card = () => page().locator("#xray-under-test");
const dialog = (name) => page().getByRole("dialog", { name });
const sent = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    xray: () => structuredClone(db.scan),
    xray_optimize_check: (p) => { refuse("xray_optimize_check", p); return { keep_days: 7, why: "Fabric's default", risk: db.risk }; },
    xray_vacuum_preview: (p) => { refuse("xray_vacuum_preview", p); return { files: 10, bytes: 1024, keep_days: 7, why: "Fabric's default", risk: db.risk }; },
    meta_project: () => db.project,
    xray_adopt_plan: () => ({ layer: "bronze", layers: ["bronze"], tables: [
      { schema: null, table: "customers", ok: true, columns: ["id"], keys: ["id"], name: "bronze.customers", exists: false, why: "" },
      { schema: null, table: "orders", ok: true, columns: ["id"], keys: ["id"], name: "bronze.orders", exists: false, why: "" }] }),
    xray_adopt: (p) => ({ adopted: [`${p.layer}.customers`], problems: ["orders: bronze.orders is already an entity"], layer: p.layer }),
  } });
  await startupReads(app);
  await page().evaluate(() => {
    state.user = "chris@contoso.com";
    const box = document.createElement("section");
    box.id = "xray-under-test";
    box.className = "card pad xray";
    document.body.prepend(box);
    window.__box = box;
  });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(db, { scan: { running: false, paused: false, progress: null, error: null, result: result() }, fail: {}, project: null, risk: PASS });
  await page().evaluate(() => {
    closeDialog(); closeDialog();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    Object.assign(xrayPage, { kind: "all", showAll: false, folded: false, batch: null });
    xrayPage.picked.clear();
    state.meta.project = null;
    window.__fdShellCalls.length = 0;
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

const load = () => page().evaluate(() => loadXray(window.__box, false));
// Replaces a page function for the test, recording its calls.
const spy = (name) => page().evaluate((n) => { window.__spied = []; window[`__${n}`] = window[n]; window[n] = (...a) => { window.__spied.push(a); }; }, name);
const unspy = (name) => page().evaluate((n) => { window[n] = window[`__${n}`]; const s = window.__spied; window.__spied = []; return s; }, name);

test("a scan still running is read again by itself until it ends", { skip }, async () => {
  db.scan = { running: true, paused: false, progress: { phase: "Reading table logs" }, error: null, result: null };
  await load();
  await card().getByText("Reading table logs").waitFor();
  db.scan = { running: false, paused: false, progress: null, error: null, result: result() };
  await card().locator(".xray-stats").waitFor({ timeout: 10000 });
  assert.ok(sent("xray").length >= 2);
  assert.deepStrictEqual(app.errors, []);
});

test("Hide details keeps the numbers and is remembered; Show details brings the findings back", { skip }, async () => {
  await load();
  await card().locator(".xray-list").waitFor();
  await card().getByRole("button", { name: "Hide details" }).click();
  await card().locator(".xray-list").waitFor({ state: "detached" });
  assert.strictEqual(await card().locator(".xray-stat").count(), 5);
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.fold.home.xray")), "1");
  await card().getByRole("button", { name: "Show details" }).click();
  await card().locator(".xray-list").waitFor();
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.fold.home.xray")), "0");
  assert.deepStrictEqual(app.errors, []);
});

test("the guide opens at the top or at a finding's own section", { skip }, async () => {
  await load();
  await card().getByRole("link", { name: "How each fix works" }).click();
  await card().locator(".xray-item", { hasText: "orders has 4,210" }).getByRole("link", { name: "How to fix this" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.filter((c) => c.cmd === "open_doc").length === 2);
  assert.deepStrictEqual(await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_doc").map((c) => c.args.page)),
    ["xray-fixes.md", "xray-fixes.md#small-files"]);
  assert.deepStrictEqual(app.errors, []);
});

test("tables it couldn't read are named; findings queued for, or added to, the central lakehouse say so", { skip }, async () => {
  db.scan.result = result({ skipped: ["Finance / LH_Paused"], shared: { lakehouse: "LH_Optimize", error: "403 Forbidden", queued: 1 } });
  await load();
  await card().getByText("Couldn't read: Finance / LH_Paused (a paused capacity, or no access).").waitFor();
  await card().getByText("LH_Optimize couldn't be reached (403 Forbidden), so 1 finding waits on this PC.", { exact: false }).waitFor();
  db.scan.result = result({ shared: { lakehouse: "LH_Optimize", findings: 6 } });
  await load();
  await card().getByText("6 lakehouse and table findings were added to LH_Optimize for Optimize > Opportunities.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Show all shows every finding and Show fewer goes back to six", { skip }, async () => {
  await load();
  assert.strictEqual(await card().locator(".xray-item").count(), 6);
  await card().getByRole("button", { name: "Show all 8" }).click();
  assert.strictEqual(await card().locator(".xray-item").count(), 8);
  await card().getByRole("button", { name: "Show fewer" }).click();
  assert.strictEqual(await card().locator(".xray-item").count(), 6);
  assert.deepStrictEqual(app.errors, []);
});

test("Open in Fabric, Review access, See cost and Open notebook each go to their place", { skip }, async () => {
  await load();
  await card().getByRole("button", { name: "Show all 8" }).click();
  await spy("openInFabric");
  await card().locator(".xray-item", { hasText: "old_sales" }).getByRole("button", { name: "Open in Fabric" }).click();
  const opened = await unspy("openInFabric");
  assert.strictEqual(opened.length, 1);
  assert.match(JSON.stringify(opened[0]), /lh1/);
  await spy("go");
  await card().locator(".xray-item", { hasText: "Admin on Sales" }).getByRole("button", { name: "Review access" }).click();
  assert.strictEqual(await page().evaluate(() => catalogPage.tab), "access");
  await card().locator(".xray-item", { hasText: "40 CU hours" }).getByRole("button", { name: "See cost" }).click();
  assert.strictEqual(await page().evaluate(() => optimizePage.tab), "cost");
  assert.deepStrictEqual(await unspy("go"), [["onelake"], ["optimize"]]);
  await spy("openNotebook");
  await card().locator(".xray-item", { hasText: "daily_load" }).getByRole("button", { name: "Open notebook" }).click();
  await toastSays(/^Set Runs on to This PC in the ribbon/);
  assert.deepStrictEqual(await unspy("openNotebook"), [["daily_load"]]);
  assert.deepStrictEqual(app.errors, []);
});

test("Clear on the pick bar unticks every table", { skip }, async () => {
  await load();
  await card().getByRole("checkbox", { name: "Optimize orders in LH_Silver" }).check();
  await card().locator(".xray-pickbar").getByText("1 of 2 tables selected.", { exact: false }).waitFor();
  await card().locator(".xray-pickbar").getByRole("button", { name: "Clear" }).click();
  await card().locator(".xray-pickbar").getByText("2 tables here can be optimized.", { exact: false }).waitFor();
  assert.strictEqual(await page().evaluate(() => xrayPage.picked.size), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Check again in Optimize and Vacuum reads the table again", { skip }, async () => {
  db.risk = WARNING;
  await load();
  await card().locator(".xray-item", { hasText: "orders has 4,210" }).getByRole("button", { name: "Optimize" }).click();
  await dialog(/Optimize/).getByRole("link", { name: "Check again" }).click();
  await page().waitForFunction(() => document.querySelector(".dialog")?.textContent.includes("Fabric's default"));
  assert.deepStrictEqual(sent("xray_optimize_check").map((p) => !!p.refresh), [false, true]);
  await page().evaluate(() => closeDialog());
  await card().locator(".xray-item", { hasText: "events has never" }).getByRole("button", { name: "Vacuum" }).click();
  await dialog(/Vacuum/).getByRole("link", { name: "Check again" }).click();
  await page().waitForFunction(() => document.querySelector(".dialog")?.textContent.includes("old files"));
  assert.deepStrictEqual(sent("xray_vacuum_preview").map((p) => !!p.refresh), [false, true]);
  assert.deepStrictEqual(app.errors, []);
});

test("Vacuum all leaves out a table it couldn't check and says so", { skip }, async () => {
  db.fail.xray_vacuum_preview = (p) => (p.table === "clicks" ? "403 Forbidden" : "");
  await load();
  await card().locator(".xray-chips").getByRole("button", { name: /^Needs vacuum/ }).click();
  await card().getByRole("button", { name: "Vacuum all 2" }).click();
  const d = dialog("Vacuum 2 tables");
  await d.getByText("clicks (couldn't be checked)", { exact: false }).waitFor();
  assert.match(await d.getByRole("button", { name: /^Vacuum \d/ }).innerText(), /Vacuum 1/);
  assert.deepStrictEqual(sent("xray_vacuum"), []);
  assert.deepStrictEqual(app.errors, []);
});

test("a batch Optimize whose check fails marks that table and starts nothing for it", { skip }, async () => {
  db.fail.xray_optimize_check = "The lakehouse's capacity can't be read";
  await load();
  await card().locator(".xray-pickbar").getByRole("button", { name: "Optimize all 2" }).click();
  await card().locator(".xray-pickbar").getByRole("button", { name: "Optimize 2 selected" }).click();
  const d = page().getByRole("dialog");
  await d.getByRole("button", { name: /^Optimize 2/ }).click();
  await card().locator(".xray-batch-item .reason", { hasText: "capacity can't be read" }).first().waitFor();
  assert.deepStrictEqual(sent("xray_optimize"), []);
  assert.deepStrictEqual(app.errors, []);
});

test("Adopt without a metadata project says to open one; a managed lakehouse has no Adopt", { skip }, async () => {
  await load();
  await card().locator(".xray-lakehouses summary").click();
  assert.strictEqual(await card().locator("tr", { hasText: "LH_Gold" }).getByText("Managed by metadata").count(), 1);
  await card().locator("tr", { hasText: "LH_Silver" }).getByRole("button", { name: "Adopt" }).click();
  const d = dialog("Adopt needs a metadata project");
  await d.getByText("Create or open a metadata project first.", { exact: false }).waitFor();
  await spy("go");
  await d.getByRole("button", { name: "Open Data mesh" }).click();
  assert.deepStrictEqual(await unspy("go"), [["metadata"]]);
  assert.deepStrictEqual(app.errors, []);
});

test("Adopt with nothing picked asks for a table; tables it skips are named", { skip }, async () => {
  db.project = { path: "/home/chris/metadata", name: "sales", layers: [] };
  await load();
  await card().locator(".xray-lakehouses summary").click();
  await card().locator("tr", { hasText: "LH_Silver" }).getByRole("button", { name: "Adopt" }).click();
  const d = dialog("Adopt LH_Silver");
  await d.getByRole("checkbox", { name: "Adopt customers" }).uncheck();
  await d.getByRole("checkbox", { name: "Adopt orders" }).uncheck();
  await d.getByRole("button", { name: "Adopt" }).click();
  await toastSays(/^Pick at least one table\.$/);
  assert.deepStrictEqual(sent("xray_adopt"), []);
  await d.getByRole("checkbox", { name: "Adopt customers" }).check();
  await d.getByRole("checkbox", { name: "Adopt orders" }).check();
  await d.getByRole("button", { name: "Adopt" }).click();
  await toastSays(/^Adopted 1\. Skipped: orders: bronze\.orders is already an entity$/);
  assert.deepStrictEqual(sent("xray_adopt")[0].tables.map((t) => t.table), ["customers", "orders"]);
  assert.deepStrictEqual(app.errors, []);
});

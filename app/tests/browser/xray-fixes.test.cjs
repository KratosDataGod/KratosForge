// #1445: the Estate X-ray's fix dialogs (app/ui/xray.js; see harness.cjs for how to run it). Optimize
// reads the check first, says it runs in Fabric and uses capacity, shows the risk check, has no way to
// start when the check blocks, and needs the box ticked when the settings ask; it sends exactly the
// table picked. Vacuum shows its dry run (files, size, days kept) before anything runs, can't start
// when nothing would be deleted, is drawn as destructive, and sends exactly the table picked. Vacuum
// all asks first, starts one job per finding shown, and names the tables it couldn't start. Adopt
// shows the plan first and sends exactly what the plan names. Pause sends xray_pause once. An engine
// refusal shows a plain message. Cancel sends nothing; opening the X-ray sends no write; axe finds
// nothing serious on the card or a dialog. Fabric is mocked; the shapes are xray_service.py's
// (sidecar/fabric_desktop/xray_service.py), its findings xray.py's, the risk check risk.py's.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const READS = ["xray", "xray_optimize_check", "xray_vacuum_preview", "xray_adopt_plan", "meta_project"];
const WRITES = ["xray_optimize", "xray_vacuum", "xray_adopt", "xray_pause"];
const card = "#xray-under-test";
const DENIED = "HTTPError: 403 Client Error: Forbidden for url: https://api.fabric.microsoft.com/v1/workspaces/w1/items/lh1/jobs/instances?jobType=TableMaintenance";

// One finding as xray.table_findings makes it, annotated by XrayService._xray_annotate (id, fixed, fix).
const where = { workspace_id: "w1", workspace_name: "Sales", lakehouse_id: "lh1", lakehouse_name: "LH_Silver" };
const fid = (f) => ["kind", "workspace_id", "lakehouse_id", "schema", "table", "person", "notebook"].map((k) => f[k] || "").join("|");
function finding(kind, table, severity, title, more = {}) {
  const action = { small_files: "optimize", vacuum: "vacuum", stale: "open" }[kind];
  const f = { ...where, schema: null, table, kind, severity, title, detail: `${title}, from the Delta log.`, action, ...more };
  return { ...f, id: fid(f), fixed: false, fix: null };
}
function fresh() {
  return {
    scanned: "2026-10-31T08:00:00+00:00", workspaces: 1, lakehouses: 2, tables: 7, partial: false, skipped: [], bytes: 5e9,
    findings: [
      finding("small_files", "orders", "high", "dbo.orders has 4,210 small files", { schema: "dbo", files: 4210, bytes: 2e8 }),
      finding("small_files", "lines", "medium", "lines has 900 small files", { files: 900, bytes: 9e7 }),
      finding("vacuum", "events", "low", "events has never been vacuumed (40 versions)", { versions: 40 }),
      finding("vacuum", "clicks", "low", "clicks has 31 versions since its last vacuum", { versions: 31, ...{ workspace_id: "w2", workspace_name: "Ops", lakehouse_id: "lh2", lakehouse_name: "LH_Raw" } }),
      finding("vacuum", "views", "low", "views has 25 versions since its last vacuum", { versions: 25 }),
    ],
    lakehouse_rows: [
      { ...where, tables: 5, bytes: 4e9, files: 6000, issues: 4, managed: false, adopted: 0 },
      { workspace_id: "w2", workspace_name: "Ops", lakehouse_id: "lh2", lakehouse_name: "LH_Raw", tables: 2, bytes: 1e9, files: 80, issues: 1, managed: false, adopted: 0 },
    ],
  };
}

// risk.analyze's result; finding() as risk.finding makes one.
const riskFinding = (check, outcome, title) => ({ check, outcome, label: outcome[0].toUpperCase() + outcome.slice(1), title,
  detail: "", evidence: [], remedy: "", source: "", observed_at: null, essential: false, measurement: null, can_override: outcome === "warning" });
const RISK = {
  pass: { id: "r-pass", result: "pass", label: "Pass", summary: "Nothing found that should stop this run",
    findings: [riskFinding("policy", "pass", "Fabric runs are allowed")], can_proceed: true, needs_acknowledgement: false, enforced: false },
  blocked: { id: "r-blk", result: "blocked", label: "Blocked", summary: "This run can't start: The capacity F8 is paused",
    findings: [riskFinding("capacity", "blocked", "The capacity F8 is paused")], can_proceed: false, needs_acknowledgement: false, enforced: false },
  warning: { id: "r-wrn", result: "warning", label: "Warning", summary: "1 thing(s) to check before you run",
    findings: [riskFinding("capacity", "warning", "The capacity F8 is 92% used")], can_proceed: true, needs_acknowledgement: true, enforced: false },
};

let scan, risk, preview, plan;
const fail = {};  // method -> the engine's error text; for xray_vacuum, a table -> error map
const copy = (x) => JSON.parse(JSON.stringify(x));
const runRecord = (p, name) => ({ status: "NotStarted", started_at: "2026-11-01T09:00:00+00:00", finished_at: null, message: "",
  kind: "Table maintenance", name, table: p.schema ? `${p.schema}.${p.table}` : p.table, workspace_id: p.workspace_id,
  workspace_name: p.workspace_name, item_id: p.lakehouse_id, job_id: `job-${p.table}` });
const refuse = (method) => { if (fail[method]) throw new Error(fail[method]); };

const mocks = {
  readiness_check: () => [],
  // XrayService.xray / xray_pause
  xray: () => copy(scan),
  xray_pause: ({ paused }) => { scan = { ...scan, paused: !!paused }; return copy(scan); },
  // XrayService.xray_optimize_check: {keep_days, why, risk}
  xray_optimize_check: () => { refuse("xray_optimize_check"); return { keep_days: 7, why: "Fabric's default", risk: copy(risk) }; },
  // XrayService.xray_optimize: the run record _log_fabric_run keeps
  xray_optimize: (p) => { refuse("xray_optimize"); return runRecord(p, `${p.lakehouse_name} ${p.table}`); },
  // XrayService.xray_vacuum_preview: {files, bytes, keep_days, why}
  xray_vacuum_preview: () => { refuse("xray_vacuum_preview"); return copy(preview); },
  // XrayService.xray_vacuum: the run record with keep_days and why
  xray_vacuum: (p) => {
    const why = typeof fail.xray_vacuum === "object" ? fail.xray_vacuum[p.table] : fail.xray_vacuum;
    if (why) throw new Error(why);
    return { ...runRecord(p, `Vacuum ${p.lakehouse_name} ${p.table}`), keep_days: 30, why: "Keep history on the silver layer" };
  },
  // MetaService.meta_project (project.summary(), cut to what Adopt reads)
  meta_project: () => ({ path: "/home/chris/metadata", name: "sales", layers: [] }),
  // XrayService.xray_adopt_plan / xray_adopt
  xray_adopt_plan: () => { refuse("xray_adopt_plan"); return copy(plan); },
  xray_adopt: (p) => {
    refuse("xray_adopt");
    return { adopted: p.tables.map((t) => `${p.layer}.${t.name.split(".").pop()}`), problems: [], layer: p.layer };
  },
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  // The app's startup reads, and its first approval polls 5 s in.
  await startupReads(app);
  for (const end = Date.now() + 15000; !["integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the app's startup polls never came");
  }
  // Home has drawn its own X-ray card signed out; the card under test is the only one that reads.
  await page().evaluate(() => {
    state.user = "chris@contoso.com";
    const box = document.createElement("section");
    box.id = "xray-under-test";
    box.className = "card pad xray";
    document.body.prepend(box);
    window.__box = box;
  });
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  scan = { running: false, paused: false, progress: null, error: null, result: fresh() };
  risk = RISK.pass;
  preview = { files: 1234, bytes: 2 * 1024 * 1024, keep_days: 30, why: "Keep history on the silver layer" };
  plan = { layer: "bronze", layers: ["bronze", "silver", "gold"], tables: [
    { schema: null, table: "customers", ok: true, columns: ["customer_id", "name", "updated_at"], keys: ["customer_id"], name: "bronze.customers", exists: false, why: "" },
    { schema: "dbo", table: "orders", ok: true, columns: ["order_id", "amount"], keys: ["order_id"], name: "bronze.orders", exists: true, why: "" },
    { schema: null, table: "raw_dump", ok: false, why: "Not a Delta table" },
    { schema: null, table: "returns", ok: false, columns: ["id"], keys: ["id"], name: "bronze.returns", exists: false, why: "Already adopted as bronze.returns" },
  ] };
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => {
    closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    xrayPage.kind = "all"; xrayPage.showAll = false; xrayPage.folded = false;
  });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const called = (from, method) => app.calls.slice(from).filter((c) => c.method === method).map((c) => c.params);
const toastText = () => page().evaluate(() => { const t = document.getElementById("toast"); return t.hidden ? "" : t.textContent; });
const waitToast = async (re) => (await page().waitForFunction((src) => {
  const t = document.getElementById("toast");
  return !t.hidden && new RegExp(src).test(t.textContent) && t.textContent;
}, re.source)).jsonValue();
// What the app sends for a finding (xray.js vacuumArgs), from the finding as the engine gave it.
const argsOf = (f) => ({ workspace_id: f.workspace_id, lakehouse_id: f.lakehouse_id, table: f.table, schema: f.schema,
  lakehouse_name: f.lakehouse_name, workspace_name: f.workspace_name, finding_id: f.id });
const byTable = (t) => scan.result.findings.find((f) => f.table === t);

async function openXray() {
  await page().evaluate(() => loadXray(window.__box, false));
  await page().waitForSelector(`${card} .xray-list .xray-item`);
}
// A finding's own fix button, by role and name inside its line (not the line's middle).
const fixButton = (title, name) => page().locator(`${card} .xray-item`, { has: page().locator("b", { hasText: title }) })
  .getByRole("button", { name, exact: true });
const dialog = () => page().locator("#dialog-root .dialog");
const dialogButton = (name) => dialog().locator(".dialog-actions").getByRole("button", { name, exact: true });

test("Opening the X-ray, its filters and its lakehouses reads only, and axe finds nothing serious", { skip }, async () => {
  const from = app.calls.length;
  await openXray();
  const titles = await page().$$eval(`${card} .xray-item b`, (bs) => bs.map((b) => b.textContent));
  assert.deepStrictEqual(titles, scan.result.findings.map((f) => f.title));
  await page().locator(`${card} .xray-chips`).getByRole("button", { name: /Needs vacuum/ }).click();
  await page().waitForSelector(`${card} button:has-text("Vacuum all 3")`);
  await page().locator(`${card} .xray-lakehouses summary b`).click();
  await page().waitForSelector(`${card} .xray-lakehouses table tbody tr`);
  // The approval polls keep coming; whatever else came is a read, once per kind.
  await new Promise((r) => setTimeout(r, 300));
  assert.deepStrictEqual(writes(from), []);
  const seen = [...new Set(app.calls.slice(from).map((c) => c.method))].filter((m) => !["integrations_pending", "editor_pending"].includes(m));
  assert.deepStrictEqual(seen.filter((m) => !READS.includes(m)), [], `only reads: ${seen}`);
  assert.ok(app.calls.slice(from).filter((c) => c.method === "xray").every((c) => !c.params.refresh && c.params.auto === true),
    "opening reads the saved scan (auto starts only the very first scan on a PC, in the engine)");
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), card), []);
  assert.deepStrictEqual(app.errors, []);
});

test("Optimize reads the check first, says it runs in Fabric and uses capacity, and sends exactly the table picked", { skip }, async () => {
  await openXray();
  const from = app.calls.length;
  await fixButton("dbo.orders has 4,210 small files", "Optimize").click();
  await dialog().locator(".risk-panel").waitFor();
  const f = byTable("orders");
  const { finding_id, ...check } = argsOf(f);
  assert.deepStrictEqual(called(from, "xray_optimize_check"), [{ ...check, refresh: false }]);
  assert.deepStrictEqual(writes(from), [], "the dialog opened without starting anything");
  assert.strictEqual(await dialog().getAttribute("aria-label"), "Optimize dbo.orders?");
  const text = await dialog().innerText();
  assert.match(text, /Optimize runs in Fabric as a table maintenance job on dbo\.orders, and uses capacity/);
  assert.match(text, /last 7 days reads[\s\S]*Fabric's default/);
  assert.match(text, /Nothing found that should stop this run/);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), []);
  await dialogButton("Optimize").click();
  await waitToast(/Optimizing dbo\.orders in Fabric/);
  assert.deepStrictEqual(writes(from), [{ method: "xray_optimize", params: argsOf(f) }]);
  assert.strictEqual(await dialog().count(), 0);
  await page().waitForFunction(() => /Fix submitted to Fabric/.test(document.querySelector("#xray-under-test .xray-list").textContent));
  assert.deepStrictEqual(app.errors, []);
});

test("Optimize can't start when the risk check blocks, and Cancel sends nothing", { skip }, async () => {
  risk = RISK.blocked;
  await openXray();
  const from = app.calls.length;
  await fixButton("lines has 900 small files", "Optimize").click();
  await dialog().locator(".risk-panel.blocked").waitFor();
  assert.match(await dialog().innerText(), /This run can't start: The capacity F8 is paused/);
  const start = dialogButton("Optimize");
  assert.ok(await start.isDisabled(), "a blocked check leaves Optimize disabled");
  assert.strictEqual(await start.getAttribute("title"), "The risk check found something that stops this run");
  await start.click({ force: true });
  await dialogButton("Cancel").click();
  assert.strictEqual(await dialog().count(), 0);
  assert.deepStrictEqual(writes(from), []);
});

test("Optimize past warnings that need the box ticked stops until it is ticked", { skip }, async () => {
  risk = RISK.warning;
  await openXray();
  const from = app.calls.length;
  await fixButton("lines has 900 small files", "Optimize").click();
  await dialog().locator(".risk-ack").waitFor();
  assert.match(await dialog().innerText(), /The capacity F8 is 92% used/);
  await dialogButton("Optimize").click();
  await waitToast(/Tick "I've looked at these" first/);
  assert.deepStrictEqual(writes(from), [], "not ticked: nothing started");
  assert.strictEqual(await dialog().count(), 1);
  await dialog().getByRole("checkbox", { name: /I've looked at these/ }).check();
  await dialogButton("Optimize").click();
  await waitToast(/Optimizing lines in Fabric/);
  assert.deepStrictEqual(writes(from), [{ method: "xray_optimize", params: argsOf(byTable("lines")) }]);
});

test("Vacuum shows its dry run before anything runs, is drawn as destructive, and sends exactly the table picked", { skip }, async () => {
  await openXray();
  const from = app.calls.length;
  await fixButton("events has never been vacuumed (40 versions)", "Vacuum").click();
  await page().waitForFunction(() => /Vacuum deletes/.test(document.querySelector("#dialog-root .dialog")?.textContent || ""));
  const f = byTable("events");
  const { workspace_id, lakehouse_id, table, schema, lakehouse_name } = f;
  assert.deepStrictEqual(called(from, "xray_vacuum_preview"), [{ workspace_id, lakehouse_id, table, schema, lakehouse_name }]);
  assert.deepStrictEqual(writes(from), []);
  const text = await dialog().innerText();
  assert.match(text, /Vacuum deletes 1,234 old files \(2\.0 MB\) that no version in the last 30 days reads\./);
  assert.match(text, /It keeps 30 days of history \(Keep history on the silver layer\)/);
  const go = dialogButton("Vacuum");
  assert.ok(await go.isEnabled());
  assert.match(await go.getAttribute("class"), /\bdanger-solid\b/, "Vacuum deletes files in OneLake, so it is drawn as destructive");
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), []);
  await go.click();
  await waitToast(/Vacuuming events in Fabric, keeping 30 days of history/);
  assert.deepStrictEqual(writes(from), [{ method: "xray_vacuum", params: argsOf(f) }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Vacuum with nothing to delete can't start, and Cancel sends nothing", { skip }, async () => {
  preview = { files: 0, bytes: 0, keep_days: 7, why: "Fabric's default" };
  await openXray();
  const from = app.calls.length;
  await fixButton("views has 25 versions since its last vacuum", "Vacuum").click();
  await page().waitForFunction(() => /Nothing to delete yet/.test(document.querySelector("#dialog-root .dialog")?.textContent || ""));
  assert.match(await dialog().innerText(), /every old file is still inside the 7 days of history this table keeps/);
  assert.ok(await dialogButton("Vacuum").isDisabled());
  await dialogButton("Cancel").click();
  // And with files to delete, Cancel still sends nothing.
  preview = { files: 3, bytes: null, keep_days: 7, why: "Fabric's default" };
  await fixButton("views has 25 versions since its last vacuum", "Vacuum").click();
  await page().waitForFunction(() => /Vacuum deletes 3 old files that/.test(document.querySelector("#dialog-root .dialog")?.textContent || ""));
  await dialogButton("Cancel").click();
  assert.strictEqual(await dialog().count(), 0);
  assert.deepStrictEqual(writes(from), []);
});

test("Vacuum all asks first, starts one job per finding shown, and names the tables not started", { skip }, async () => {
  await openXray();
  await page().locator(`${card} .xray-chips`).getByRole("button", { name: /Needs vacuum/ }).click();
  const from = app.calls.length;
  await page().locator(card).getByRole("button", { name: "Vacuum all 3" }).click();
  await dialog().waitFor();
  assert.strictEqual(await dialog().getAttribute("aria-label"), "Vacuum 3 tables");
  assert.match(await dialog().innerText(), /its own table maintenance job/);
  assert.match(await dialogButton("Vacuum 3").getAttribute("class"), /\bdanger-solid\b/);
  assert.deepStrictEqual(writes(from), [], "asks before starting any");
  await dialogButton("Cancel").click();
  assert.deepStrictEqual(writes(from), [], "Cancel sends nothing");
  fail.xray_vacuum = { clicks: DENIED };
  await page().locator(card).getByRole("button", { name: "Vacuum all 3" }).click();
  await dialogButton("Vacuum 3").click();
  assert.strictEqual(await waitToast(/Not started/), "Started 2 vacuums. Not started: clicks");
  const shown = ["events", "clicks", "views"].map((t) => ({ method: "xray_vacuum", params: argsOf(byTable(t)) }));
  assert.deepStrictEqual(writes(from), shown, "one per finding shown, each its own table");
  assert.deepStrictEqual(app.errors, []);
});

test("Adopt shows the plan first and sends exactly what the plan names", { skip }, async () => {
  await openXray();
  await page().locator(`${card} .xray-lakehouses summary b`).click();
  const from = app.calls.length;
  await page().locator(`${card} .xray-lakehouses tbody tr`, { has: page().locator("td", { hasText: "LH_Silver" }) })
    .getByRole("button", { name: "Adopt" }).click();
  await dialog().locator("tr[data-table]").first().waitFor();
  assert.deepStrictEqual(called(from, "xray_adopt_plan"), [{ workspace_id: "w1", lakehouse_id: "lh1", lakehouse_name: "LH_Silver", workspace_name: "Sales" }]);
  assert.deepStrictEqual(writes(from), []);
  const rows = await dialog().locator("tr[data-table]").evaluateAll((trs) => trs.map((tr) => ({
    table: tr.querySelector(".mono").textContent, checked: tr.querySelector("input[type=checkbox]").checked,
    disabled: tr.querySelector("input[type=checkbox]").disabled, name: tr.querySelector("input.name")?.value ?? null,
    keys: tr.querySelector("input.keys")?.value ?? null, why: tr.lastElementChild.textContent })));
  assert.deepStrictEqual(rows, [
    { table: "customers", checked: true, disabled: false, name: "bronze.customers", keys: "customer_id", why: "" },
    { table: "dbo.orders", checked: false, disabled: false, name: "bronze.orders_adopted", keys: "order_id", why: "bronze.orders exists, so it gets a new name" },
    { table: "raw_dump", checked: false, disabled: true, name: null, keys: null, why: "Not a Delta table" },
    { table: "returns", checked: false, disabled: true, name: null, keys: null, why: "Already adopted as bronze.returns" },
  ]);
  assert.strictEqual(await dialog().locator("select").inputValue(), "bronze");
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), []);
  await dialogButton("Adopt").click();
  await waitToast(/Adopted 1 table into bronze/);
  assert.deepStrictEqual(writes(from), [{ method: "xray_adopt", params: { workspace_id: "w1", lakehouse_id: "lh1",
    lakehouse_name: "LH_Silver", workspace_name: "Sales", layer: "bronze",
    tables: [{ schema: null, table: "customers", name: "bronze.customers", keys: ["customer_id"] }] } }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Pause on a running scan sends xray_pause once", { skip }, async () => {
  scan = { running: true, paused: false, progress: { phase: "Reading table logs", done: 10, total: 40 }, error: null, result: null };
  const from = app.calls.length;
  await page().evaluate(() => loadXray(window.__box, false));
  await page().locator(card).getByRole("button", { name: "Pause", exact: true }).click();
  await page().waitForSelector(`${card} .bar.paused`);
  assert.deepStrictEqual(writes(from), [{ method: "xray_pause", params: { paused: true } }]);
  // Paused, the card stops reading; stop the scan for the tests after.
  await new Promise((r) => setTimeout(r, 2300));
  assert.deepStrictEqual(writes(from).length, 1);
  scan = { running: false, paused: false, progress: null, error: null, result: fresh() };
  await page().evaluate(() => clearTimeout(xrayPage.timer));
});

test("An engine refusal shows a plain message, not the raw error or [object Object]", { skip }, async () => {
  await openXray();
  const plainOf = (raw) => page().evaluate((raw) => friendlyError(raw).text, raw);
  const plain = await plainOf(DENIED);
  assert.doesNotMatch(plain, /HTTPError|403|https?:/, `friendlyError says it in words: ${plain}`);
  const from = app.calls.length;
  // Vacuum refused by Fabric: the dialog stays, its button comes back.
  fail.xray_vacuum = DENIED;
  await fixButton("events has never been vacuumed (40 versions)", "Vacuum").click();
  await page().waitForFunction(() => /Vacuum deletes/.test(document.querySelector("#dialog-root .dialog")?.textContent || ""));
  await dialogButton("Vacuum").click();
  let shown = await waitToast(/^Vacuum not started/);
  assert.strictEqual(shown.split("\n")[0], `Vacuum not started: ${plain}`);
  assert.ok(await dialogButton("Vacuum").isEnabled(), "the button comes back to try again");
  await dialogButton("Cancel").click();
  // Optimize refused by the engine's own check (xray_service._xray_keep_days).
  fail.xray_optimize = "RuntimeError: Couldn't read the metadata project (TOMLDecodeError: bad), so the days of history its layers keep aren't known. Nothing was started; fix the project and try again.";
  await fixButton("lines has 900 small files", "Optimize").click();
  await dialog().locator(".risk-panel").waitFor();
  await dialogButton("Optimize").click();
  shown = await waitToast(/^Optimize not started/);
  // The check's dry run refused: the dialog closes and says why.
  fail.xray_vacuum_preview = DENIED;
  await dialogButton("Cancel").click();
  await fixButton("views has 25 versions since its last vacuum", "Vacuum").click();
  const checkMsg = await waitToast(/^Could not check the table/);
  assert.strictEqual(await dialog().count(), 0);
  // Adopt refused.
  fail.xray_adopt = "ValueError: gold2 is not a layer";
  await page().locator(`${card} .xray-lakehouses summary b`).click();
  await page().locator(`${card} .xray-lakehouses tbody tr`, { has: page().locator("td", { hasText: "LH_Raw" }) })
    .getByRole("button", { name: "Adopt" }).click();
  await dialog().locator("tr[data-table]").first().waitFor();
  await dialogButton("Adopt").click();
  const adoptMsg = await waitToast(/^Not adopted/);
  for (const m of [shown, checkMsg, adoptMsg]) {
    assert.doesNotMatch(m, /\[object Object\]|undefined|HTTPError|RuntimeError|ValueError|Traceback/, m);
  }
  assert.match(checkMsg, new RegExp(`^Could not check the table: ${plain.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`));
  assert.deepStrictEqual(app.errors, []);
  assert.deepStrictEqual(writes(from).map((w) => w.method), ["xray_vacuum", "xray_optimize", "xray_adopt"]);
});

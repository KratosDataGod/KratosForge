// #1453: going ahead with X-ray Optimize past the risk check's warnings is recorded, like every
// other run past warnings (app/ui/xray.js optimizeDialog; see harness.cjs for how to run it).
// risk_acknowledge can't re-check an X-ray table (its "xray:<ws>/<lh>/<table>" id isn't a workload
// id), so xray_optimize itself runs the check again and records going ahead: the app sends the id
// of the assessment it showed as risk_id. Tested: a warning sends xray_optimize with that risk id
// (and the box ticked when the settings ask), a pass sends its own id, and neither sends
// risk_acknowledge; the engine refusing changed or blocked findings shows a plain message, keeps
// the dialog open and marks nothing submitted; opening the dialog and Cancel send no write; axe
// finds nothing serious on the warning dialog. The engine is mocked; the shapes are
// sidecar/fabric_desktop/xray_service.py's (xray_optimize_check, xray_optimize), its findings
// xray.py's, the risk check risk.py's (analyze), the refusals xray_optimize's own messages.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const READS = ["xray", "xray_optimize_check"];
const WRITES = ["xray_optimize", "risk_acknowledge"];
const card = "#xray-under-test";

// One finding as xray.table_findings makes it, annotated by XrayService._xray_annotate (id, fixed, fix).
const where = { workspace_id: "w1", workspace_name: "Sales", lakehouse_id: "lh1", lakehouse_name: "LH_Silver" };
const fid = (f) => ["kind", "workspace_id", "lakehouse_id", "schema", "table", "person", "notebook"].map((k) => f[k] || "").join("|");
function finding(table, title, more = {}) {
  const f = { ...where, schema: null, table, kind: "small_files", severity: "high", title, detail: `${title}, from the Delta log.`,
    action: "optimize", ...more };
  return { ...f, id: fid(f), fixed: false, fix: null };
}
const fresh = () => ({
  scanned: "2026-10-31T08:00:00+00:00", workspaces: 1, lakehouses: 1, tables: 2, partial: false, skipped: [], bytes: 5e9,
  findings: [finding("orders", "dbo.orders has 4,210 small files", { schema: "dbo", files: 4210, bytes: 2e8 }),
    finding("lines", "lines has 900 small files", { files: 900, bytes: 9e7 })],
  lakehouse_rows: [{ ...where, tables: 2, bytes: 5e9, files: 5110, issues: 2, managed: false, adopted: 0 }],
});

// risk.analyze's result for xray_optimize_check's facts (workload_id "xray:<ws>/<lh>/<label>").
const riskFinding = (check, outcome, title) => ({ check, outcome, label: outcome[0].toUpperCase() + outcome.slice(1), title,
  detail: "", evidence: [], remedy: "", source: "", observed_at: null, essential: false, measurement: null, can_override: outcome === "warning" });
const assessed = (id, result, summary, findings, more = {}) => ({ id, result, label: result[0].toUpperCase() + result.slice(1), summary,
  workload_id: "xray:w1/lh1/dbo.orders", workload: "LH_Silver dbo.orders", action: "run_in_fabric", action_label: "Optimize",
  env: null, where: "fabric", findings, can_proceed: result !== "blocked", needs_acknowledgement: false, enforced: false, ...more });
const RISK = {
  pass: assessed("r-pass", "pass", "Nothing found that should stop this run", [riskFinding("policy", "pass", "Fabric runs are allowed")]),
  warning: assessed("r-wrn", "warning", "1 thing(s) to check before you run",
    [riskFinding("capacity", "warning", "fabcapdev was paused when last read")]),
};
// xray_optimize's refusals (sidecar/fabric_desktop/xray_service.py), as the server sends str(exc).
const CHANGED = "The findings changed since you looked. Check them again before you go ahead.";
const BLOCKED = "A blocked run can't be started: This run can't start: fabcapdev is paused";

let scan, risk, fail;
const copy = (x) => JSON.parse(JSON.stringify(x));
const mocks = {
  readiness_check: () => [],
  xray: () => copy(scan),
  // XrayService.xray_optimize_check: {keep_days, why, risk}
  xray_optimize_check: () => ({ keep_days: 7, why: "Fabric's default", risk: copy(risk) }),
  // XrayService.xray_optimize: the run record _log_fabric_run keeps, or its refusal
  xray_optimize: (p) => {
    if (fail) throw new Error(fail);
    return { status: "NotStarted", started_at: "2026-11-01T09:00:00+00:00", finished_at: null, message: "", kind: "Table maintenance",
      name: `${p.lakehouse_name} ${p.table}`, table: p.schema ? `${p.schema}.${p.table}` : p.table, workspace_id: p.workspace_id,
      workspace_name: p.workspace_name, item_id: p.lakehouse_id, job_id: `job-${p.table}` };
  },
  // RiskService.risk_acknowledge: must not be called for X-ray (it can't re-check an X-ray id)
  risk_acknowledge: () => { throw new Error("'xray:w1/lh1/dbo.orders' isn't a workload id"); },
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await startupReads(app);
  for (const end = Date.now() + 15000; !["integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the app's startup polls never came");
  }
  // Home draws its own X-ray card signed out; the card under test is the only one that reads.
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
  fail = null;
  await page().evaluate(() => {
    closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    xrayPage.kind = "all"; xrayPage.showAll = false; xrayPage.folded = false;
  });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const waitToast = async (re) => (await page().waitForFunction((src) => {
  const t = document.getElementById("toast");
  return !t.hidden && new RegExp(src).test(t.textContent) && t.textContent;
}, re.source)).jsonValue();
// What the app sends for a finding (xray.js vacuumArgs), plus the id of the assessment it showed.
const argsOf = (f, risk_id) => ({ workspace_id: f.workspace_id, lakehouse_id: f.lakehouse_id, table: f.table, schema: f.schema,
  lakehouse_name: f.lakehouse_name, workspace_name: f.workspace_name, finding_id: f.id, risk_id });
const orders = () => scan.result.findings[0];
async function openOptimize() {
  await page().evaluate(() => loadXray(window.__box, false));
  await page().waitForSelector(`${card} .xray-list .xray-item`);
  const from = app.calls.length;
  // The finding's own Optimize button, by role and name inside its line (not the line's middle).
  await page().locator(`${card} .xray-item`, { has: page().locator("b", { hasText: "dbo.orders has 4,210 small files" }) })
    .getByRole("button", { name: "Optimize", exact: true }).click();
  await dialog().locator(".risk-panel").waitFor();
  return from;
}
const dialog = () => page().locator("#dialog-root .dialog");
const dialogButton = (name) => dialog().locator(".dialog-actions").getByRole("button", { name, exact: true });

test("Opening Optimize past warnings and cancelling reads only, and axe finds nothing serious", { skip }, async () => {
  risk = RISK.warning;
  const from = await openOptimize();
  assert.match(await dialog().innerText(), /fabcapdev was paused when last read/);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), []);
  await dialogButton("Cancel").click();
  assert.strictEqual(await dialog().count(), 0);
  assert.deepStrictEqual(writes(from), []);
  const seen = [...new Set(app.calls.slice(from).map((c) => c.method))].filter((m) => !["integrations_pending", "editor_pending"].includes(m));
  assert.deepStrictEqual(seen.filter((m) => !READS.includes(m)), [], `only reads: ${seen}`);
  assert.deepStrictEqual(app.errors, []);
});

test("Optimize past warnings sends the id of the assessment shown, so the engine records it", { skip }, async () => {
  risk = RISK.warning;
  const from = await openOptimize();
  await dialogButton("Optimize").click();
  await waitToast(/Optimizing dbo\.orders in Fabric/);
  assert.deepStrictEqual(writes(from), [{ method: "xray_optimize", params: argsOf(orders(), "r-wrn") }],
    "one xray_optimize with the warning's risk id; no risk_acknowledge (it can't re-check an X-ray table)");
  assert.strictEqual(await dialog().count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Optimize past warnings that need the box ticked sends nothing until it is ticked", { skip }, async () => {
  risk = { ...RISK.warning, needs_acknowledgement: true };
  const from = await openOptimize();
  await dialogButton("Optimize").click();
  await waitToast(/Tick "I've looked at these" first/);
  assert.deepStrictEqual(writes(from), []);
  await dialog().getByRole("checkbox", { name: /I've looked at these/ }).check();
  await dialogButton("Optimize").click();
  await waitToast(/Optimizing dbo\.orders in Fabric/);
  assert.deepStrictEqual(writes(from), [{ method: "xray_optimize", params: argsOf(orders(), "r-wrn") }]);
});

test("Optimize on a pass sends its own id and no acknowledgement", { skip }, async () => {
  const from = await openOptimize();
  await dialogButton("Optimize").click();
  await waitToast(/Optimizing dbo\.orders in Fabric/);
  assert.deepStrictEqual(writes(from), [{ method: "xray_optimize", params: argsOf(orders(), "r-pass") }]);
});

for (const [why, text] of [["changed", CHANGED], ["blocked", BLOCKED]]) {
  test(`The engine refusing ${why} findings shows a plain message and starts nothing`, { skip }, async () => {
    risk = RISK.warning;
    fail = text;
    const from = await openOptimize();
    await dialogButton("Optimize").click();
    const shown = await waitToast(/^Optimize not started/);
    const plain = await page().evaluate((raw) => friendlyError(raw).text, text);
    assert.strictEqual(shown.split("\n")[0], `Optimize not started: ${plain}`);
    assert.doesNotMatch(shown, /\[object Object\]|Error:/);
    assert.match(shown, why === "changed" ? /Check them again/ : /can't be started/);
    assert.strictEqual(await dialog().count(), 1, "the dialog stays, with Check again");
    assert.ok(await dialogButton("Optimize").isEnabled(), "the button comes back");
    assert.deepStrictEqual(writes(from).map((w) => w.method), ["xray_optimize"]);
    assert.doesNotMatch(await page().locator(`${card} .xray-list`).innerText(), /Fix submitted/);
    assert.deepStrictEqual(app.errors, []);
  });
}

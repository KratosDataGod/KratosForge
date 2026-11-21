// Estate X-ray: tick several "small files" findings and Optimize them together (app/ui/xray.js
// xrayPickBar, optimizeBatchDialog, optimizeBatch; see harness.cjs for how to run it).
// Tested: only findings Optimize fixes get a box, and only those the chip and search show; Optimize all
// ticks every one shown and opens the whole list so some can be unticked; the confirm names the count
// and every table, and Cancel writes nothing; the batch starts at most XRAY_BATCH.atOnce jobs before
// one ends, runs each table's own risk check first and starts only a pass (warnings, a block or a
// check that couldn't run are skipped, never sent), reports each table's result, and Stop starts
// nothing more while the running jobs are still followed to the end; axe finds nothing serious on the
// confirm. The engine is mocked; the shapes are sidecar/fabric_desktop/xray_service.py's
// (xray_optimize_check, xray_optimize), cloud_service.py's (fabric_runs), xray.py's findings and
// risk.py's analyze.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");
// axe covers dialogs: xray.js:optimizeBatchDialog

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const card = "#xray-under-test";

const where = { workspace_id: "w1", workspace_name: "Sales", lakehouse_id: "lh1", lakehouse_name: "LH_Silver" };
const fid = (f) => ["kind", "workspace_id", "lakehouse_id", "schema", "table", "person", "notebook"].map((k) => f[k] || "").join("|");
function finding(table, more = {}) {
  const f = { ...where, schema: null, table, kind: "small_files", severity: "high", title: `${table} has 900 small files`,
    detail: "Averaging 9 KB each.", action: "optimize", files: 900, bytes: 9e6, ...more };
  return { ...f, id: fid(f), fixed: false, fix: null };
}
const TABLES = ["t1", "t2", "t3", "t4", "t5", "t6", "t7"];
const fresh = () => ({
  scanned: "2026-11-10T08:00:00+00:00", workspaces: 1, lakehouses: 1, tables: 9, partial: false, skipped: [], bytes: 5e9,
  findings: [...TABLES.map((t) => finding(t)),
    finding("old_log", { kind: "vacuum", severity: "medium", title: "old_log needs vacuum", action: "vacuum" }),
    finding("quiet", { kind: "stale", severity: "low", title: "quiet hasn't changed in 90 days", action: "open" })],
  lakehouse_rows: [{ ...where, tables: 9, bytes: 5e9, files: 6300, issues: 9, managed: false, adopted: 0 }],
});
const assessed = (id, result, summary) => ({ id, result, label: result, summary, findings: [], can_proceed: result !== "blocked",
  needs_acknowledgement: false, enforced: false });

let scan, riskFor, failFor, jobs;
const copy = (x) => JSON.parse(JSON.stringify(x));
const mocks = {
  readiness_check: () => [],
  xray: () => copy(scan),
  xray_optimize_check: (p) => ({ keep_days: 7, why: "Fabric's default", risk: riskFor[p.table] || assessed(`r-${p.table}`, "pass", "Nothing found") }),
  xray_optimize: (p) => {
    if (failFor[p.table]) throw new Error(failFor[p.table]);
    jobs[`job-${p.table}`] = "NotStarted";
    return { status: "NotStarted", kind: "Table maintenance", table: p.table, workspace_id: p.workspace_id, item_id: p.lakehouse_id, job_id: `job-${p.table}` };
  },
  fabric_runs: (p) => (p.job_ids || []).map((id) => ({ job_id: id, status: jobs[id] || "NotStarted", message: jobs[id] === "Failed" ? "Out of capacity" : "" })),
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await startupReads(app);
  for (const end = Date.now() + 15000; !["integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the app's startup polls never came");
  }
  await page().evaluate(() => {
    state.user = "chris@contoso.com";
    const box = document.createElement("section");
    box.id = "xray-under-test";
    box.className = "card pad xray";
    document.body.prepend(box);
    window.__box = box;
    XRAY_BATCH.pollMs = 60;
  });
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  scan = { running: false, paused: false, progress: null, error: null, result: fresh() };
  riskFor = {};
  failFor = {};
  jobs = {};
  await page().evaluate(() => {
    closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    if (xrayPage.batch) { xrayPage.batch.closed = true; xrayPage.batch.wake?.(); }
    xrayPage.batch = null; xrayPage.picked.clear();
    xrayPage.kind = "all"; xrayPage.showAll = false; xrayPage.folded = false;
  });
  await page().evaluate(() => loadXray(window.__box, false));
  await page().waitForSelector(`${card} .xray-list .xray-item`);
});

const dialog = () => page().locator("#dialog-root .dialog");
const bar = () => page().locator(`${card} .xray-pickbar`);
const calls = (from, method) => app.calls.slice(from).filter((c) => c.method === method).map((c) => c.params.table);
const rowBox = (table) => page().getByRole("checkbox", { name: `Optimize ${table} in LH_Silver` });
const batchText = () => page().locator(`${card} .xray-batch`).innerText();
async function waitFinished() {
  await page().waitForFunction(() => xrayPage.batch?.finished, null, { timeout: 10000 });
}

test("Only findings Optimize fixes get a box, and only those the chip shows", { skip }, async () => {
  assert.strictEqual(await page().locator(`${card} .xray-item input.xray-pick`).count(), 6, "six shown, all small files");
  await page().getByRole("button", { name: /Show all 9/ }).click();
  assert.strictEqual(await page().locator(`${card} .xray-item input.xray-pick`).count(), 7, "the vacuum and stale lines get none");
  await page().locator(`${card} .xray-chips button`, { hasText: "Needs vacuum" }).click();
  assert.strictEqual(await bar().count(), 0, "nothing on the Needs vacuum chip can be optimized");
  assert.strictEqual(await page().locator(`${card} .xray-item input.xray-pick`).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Optimize all ticks every table shown and opens the list; unticking leaves one out", { skip }, async () => {
  const from = app.calls.length;
  await bar().getByRole("button", { name: "Optimize all 7" }).click();
  assert.strictEqual(await page().locator(`${card} .xray-item input.xray-pick:checked`).count(), 7, "the whole list opens, all ticked");
  await rowBox("t3").uncheck();
  assert.match(await bar().innerText(), /6 of 7 tables selected/);
  assert.strictEqual(await bar().locator("input").evaluate((el) => el.indeterminate), true);
  await bar().getByRole("button", { name: "Optimize 6 selected" }).click();
  const text = await dialog().innerText();
  assert.match(text, /Optimize 6 tables\?/);
  assert.match(text, /At most 3 run at once/);
  assert.doesNotMatch(text, /\bt3\b/, "the unticked table isn't named");
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), []);
  await dialog().getByRole("button", { name: "Cancel", exact: true }).click();
  assert.deepStrictEqual(calls(from, "xray_optimize").concat(calls(from, "xray_optimize_check")), [], "Cancel reads and writes nothing");
  assert.deepStrictEqual(app.errors, []);
});

test("A search narrows what Select all ticks", { skip }, async () => {
  await page().evaluate(() => { state.query = "t2 has"; drawXray(window.__box, xrayPage.data); });
  try {
    assert.match(await bar().innerText(), /1 table here can be optimized/);
    await bar().locator("input").check();
    assert.strictEqual(await bar().getByRole("button", { name: "Optimize 1 selected" }).count(), 1);
  } finally {
    await page().evaluate(() => { state.query = ""; });
  }
});

test("The batch keeps three running, skips what the risk check doesn't pass, and reports each table", { skip }, async () => {
  riskFor = { t2: assessed("r-t2", "warning", "1 thing to check"), t4: assessed("r-t4", "blocked", "fabcapdev is paused"),
    t5: assessed("r-t5", "unknown", "The risk check couldn't run") };
  failFor = { t6: "The findings changed since you looked. Check them again before you go ahead." };
  const from = app.calls.length;
  await bar().getByRole("button", { name: "Optimize all 7" }).click();
  await bar().getByRole("button", { name: "Optimize 7 selected" }).click();
  await dialog().getByRole("button", { name: "Optimize 7", exact: true }).click();
  // t1, t3 and t6's start were tried; t6 was refused, so t7 fills the third place. Nothing ends yet.
  await page().waitForFunction(() => xrayPage.batch?.items.filter((i) => i.state === "running").length === 3);
  await new Promise((r) => setTimeout(r, 300));
  assert.deepStrictEqual(calls(from, "xray_optimize"), ["t1", "t3", "t6", "t7"], "only passes are sent, never more than 3 running");
  assert.deepStrictEqual(app.calls.slice(from).filter((c) => c.method === "xray_optimize").map((c) => c.params.risk_id),
    ["r-t1", "r-t3", "r-t6", "r-t7"]);
  assert.strictEqual(await page().getByRole("button", { name: /^Optimize all/ }).count(), 0, "no second batch while one runs");
  jobs["job-t1"] = "Completed"; jobs["job-t3"] = "Failed"; jobs["job-t7"] = "Completed";
  await waitFinished();
  const text = await batchText();
  assert.match(text, /Optimize batch finished/);
  assert.match(text, /7 of 7 ended/);
  assert.match(text, /Optimized\s+t1/);
  assert.match(text, /Failed\s+t3[\s\S]*Fabric stopped it: out of capacity\. Try Optimize again when the capacity is less busy\./);
  assert.match(text, /2 optimized, 1 failed, 1 couldn't start, 3 skipped/);
  assert.match(text, /Skipped\s+t2[\s\S]*warnings/);
  assert.match(text, /Skipped\s+t4[\s\S]*blocks it: fabcapdev is paused/);
  assert.match(text, /Skipped\s+t5[\s\S]*couldn't run/);
  assert.match(text, /Couldn't start\s+t6[\s\S]*Check them again/);
  // Optimized tables leave the findings; the others keep their own Optimize.
  const list = await page().locator(`${card} .xray-list`).innerText();
  assert.doesNotMatch(list, /t1 has|t7 has/);
  assert.match(list, /Fabric stopped it: out of capacity\./);
  assert.deepStrictEqual(app.errors, []);
});

test("Stop starts nothing more and still follows the running jobs to the end", { skip }, async () => {
  const from = app.calls.length;
  await bar().getByRole("button", { name: "Optimize all 7" }).click();
  await bar().getByRole("button", { name: "Optimize 7 selected" }).click();
  await dialog().getByRole("button", { name: "Optimize 7", exact: true }).click();
  await page().waitForFunction(() => xrayPage.batch?.items.filter((i) => i.state === "running").length === 3);
  await page().locator(`${card} .xray-batch`).getByRole("button", { name: "Stop" }).click();
  assert.match(await batchText(), /Stopping: waiting for 3 running in Fabric/);
  jobs["job-t1"] = "Completed"; jobs["job-t2"] = "Completed"; jobs["job-t3"] = "Completed";
  await waitFinished();
  assert.deepStrictEqual(calls(from, "xray_optimize"), ["t1", "t2", "t3"]);
  const text = await batchText();
  assert.match(text, /Optimize batch stopped/);
  assert.match(text, /3 optimized, 4 not started/);
  await page().locator(`${card} .xray-batch`).getByRole("button", { name: "Close" }).click();
  assert.strictEqual(await page().locator(`${card} .xray-batch`).count(), 0);
  assert.match(await bar().innerText(), /4 tables here can be optimized/, "the four not started can be picked again");
  assert.doesNotMatch(await bar().innerText(), /null/);
  assert.deepStrictEqual(app.errors, []);
});

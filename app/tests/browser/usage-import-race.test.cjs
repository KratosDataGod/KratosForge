// #1459: the usage import dialog in app/ui/estate.js (Optimize > Cost and savings > Import usage file),
// in a real browser (see harness.cjs for how to run it). Editing "Times are in" and then picking a
// preview choice without leaving the field starts two usage_preview calls at once. Only the latest
// preview's answer is shown: an older answer that arrives later is ignored, Import stays enabled, and
// Import sends the options of the preview shown. Previewing sends no write, axe finds nothing serious
// on the dialog, and there are no page errors.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const READS = ["cost_report", "optimize_status"];  // optimize_status: the Optimize page's band
const WRITES = ["usage_import", "usage_import_delete"];
const LOOKUPS = ["usage_preview"];  // asks the engine without changing anything, once the person picks a file

// The usage file, as a Capacity Metrics app Timepoint export (sidecar/tests/test_usage_import.py OPS).
const CSV = `Workspace,Item kind,Item name,Operation,Start,End,Status,User,Duration (s),Total CU (s),Timepoint CU (s),Billing type,Operation ID
Sales Dev,Notebook,nb_sales,Notebook run,10/06/2026 2:00:05 PM,10/06/2026 2:04:00 PM,Succeeded,ana@contoso.com,235,"1,880.5",0.65,Billable,op-1
Sales Dev,Notebook,nb_other,Notebook run,10/06/2026 3:00:05 PM,10/06/2026 3:01:00 PM,Succeeded,bo@contoso.com,55,440,0.1,Non-billable,op-3
`;
const measured = (value, metric = "fabric_capacity_cu") => ({ classification: "measured", label: "Measured", metric, unit: "cu_hours", value,
  currency: null, parts: 2, unknown_parts: 0, complete: true, by_classification: { measured: 2 }, assumptions: [],
  methods: ["Total CU (s) column of the Operations (Timepoint page) export"], sources: ["Capacity Metrics app export (metrics.csv)"] });
const zero = (metric, unit, currency = null) => ({ classification: "measured", label: "Measured", metric, unit, value: 0, currency, parts: 0,
  unknown_parts: 0, complete: true, by_classification: {}, assumptions: [], methods: [], sources: [] });
// usage/store.py Store.summaries (SUMMARY keys), as usage_service.usage_import answers and imports_list lists.
const IMPORT = {
  id: "u-0948f39629fcafb8", provider: "metrics_app", provider_label: "Capacity Metrics app export", layout: "operations",
  layout_label: "Operations (Timepoint page)", file_name: "metrics.csv", sha256: "0948f39629fcafb857dbe15b912526e9a75fe273fa105caa253b95f856c98d5c",
  imported_at: new Date(Date.now() - 3600e3).toISOString(), options: { tz: "UTC", decimal: ".", date_order: "mdy", period_start: null, period_end: null },
  period: { start: "2026-10-06T14:00:05+00:00", end: "2026-10-06T15:01:00+00:00" }, workspaces: ["Sales Dev"], capacities: [], rows: 2,
  total: measured(0.644583), cost: [], skipped: [], notes: [],
};
// estate_service.cost_report: costs.report(...) | {unavailable, capacities, usage}; usage is
// usage_service._usage_reconcile (usage/reconcile.py reconcile | {"imports_list"}). Captured from the engine.
function costReport() {
  const since = new Date(Date.now() - 30 * 864e5).toISOString(), now = new Date().toISOString();
  const imports = db.imports;
  return {
    days: 30, since, settings: { price_per_cu_hour: 0.18, currency: "USD", python_cu: 1.0, spark_cu: 8.0 },
    total: { runs: 0, seconds: 0, fabric_runs: 0, local_runs: 0, unknown_runs: 0, uncalibrated_runs: 0, cu_hours: 0, cost: 0,
      scenario_cu_hours: 0, scenario_saved: 0, evidence: { fabric_job_cu: zero("fabric_job_cu", "cu_hours"),
        fabric_cost: zero("fabric_cost", "currency", "USD"), avoided_cu: zero("fabric_job_cu_avoided", "cu_hours"),
        avoided_cost: zero("fabric_cost_avoided", "currency", "USD") } },
    by_env: [], by_item: [], by_day: [], unavailable: {}, capacities: { observed_at: null, environments: [], partial: false },
    usage: {
      period: { start: since, end: now }, imports: imports.length, rows: imports.length ? 2 : 0, rows_outside_period: 0,
      imported: imports.length ? measured(0.644583) : null,
      matched: { rows: 0, runs: 0, measured: null, estimated: null, measured_to_estimate: null },
      unmatched: { rows: imports.length ? 2 : 0, measured: imports.length ? measured(0.644583) : null,
        by_reason: imports.length ? { "No run of this item started from this app at that time": 2 } : {} },
      runs: { total: 0, matched: 0, no_row: 0, not_covered: 0, no_item_name: 0 }, by_run: [], tolerance_s: 300, billed: [],
      imports_list: imports.map((i) => ({ ...i })),
    },
  };
}
// usage_service.usage_preview: usage.read without its rows. The dates in CSV can be read both ways, so
// the first look asks for the date order (usage/reader.py NeedsChoice "date_order").
function preview({ file_name, content, options }) {
  const base = { file_name, sha256: IMPORT.sha256, provider: { id: "metrics_app", label: "Capacity Metrics app export" },
    layout: { id: "operations", label: "Operations (Timepoint page)", timed: true },
    columns: { used: { workspace: "Workspace", item: "Item name", start: "Start", end: "End", cu: "Total CU (s)" },
      ignored: ["User", "Timepoint CU (s)"], not_stored: ["User"], missing: [] }, notes: [] };
  if (!content.startsWith("Workspace,")) return { ...base, ready: false, problem: "This isn't a usage export.", needs: null };
  // usage/reader.py zone: a time zone it can't read is a FileProblem, so the preview isn't ready.
  const tz = String(options.tz || "UTC").trim().toUpperCase(), m = /^([+-])(\d{1,2}):?(\d{2})?$/.exec(tz);
  if (!["UTC", "Z", "GMT", "+00:00", "00:00"].includes(tz) && (!m || +m[2] > 14 || +(m[3] || 0) > 59)) {
    return { ...base, ready: false, needs: null, problem: `'${options.tz}' isn't a time zone offset. Use UTC or a form like +02:00.` };
  }
  if (!options.date_order) {
    return { ...base, ready: false, needs: { option: "date_order", choices: { mdy: "Month first: 04/30/2026", dmy: "Day first: 30/04/2026" } },
      problem: "The dates can be read two ways (is 03/04 the 3rd of April or March 4th?). Choose the date order the export uses." };
  }
  return { ...base, ready: true, problem: null, needs: null, options: { decimal: ".", period_start: null, period_end: null, ...options },
    rows_read: 2, to_import: 2, duplicates_in_file: 0, already_imported: 0, replaces_earlier: 0, skipped: [],
    period: IMPORT.period, workspaces: ["Sales Dev"], capacities: [], items: 2, total: measured(0.644583), cost: [] };
}

let db;
// usage_preview calls the test holds: each waits until the test releases it, in any order.
let holding = false;
const held = [];
const release = (i) => held[i].release();

const mocks = {
  cost_report: () => costReport(),
  usage_preview: (p) => {
    if (!holding) return preview(p);
    return new Promise((resolve) => held.push({ options: p.options, release: () => resolve(preview(p)) }));
  },
  // usage_service.usage_import: usage.read, then the import's Store.summaries row.
  usage_import: (p) => {
    const ok = preview(p);
    if (!ok.ready) throw new Error(`ValueError: ${ok.problem}`);
    db.imports.push({ ...IMPORT, options: ok.options });
    return { ...IMPORT, options: ok.options, already_imported: 0, duplicates_in_file: 0 };
  },
};

let baseline;  // the methods the app calls on its own

before(async () => {
  if (skip) return;
  db = { imports: [] };
  app = await openApp({ mocks });
  // The app's startup reads, and its first approval polls 5 s in (integrations_pending, editor_pending).
  await startupReads(app);
  for (const end = Date.now() + 15000; !["integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the app's startup polls never came");
  }
  baseline = new Set(app.calls.map((c) => c.method));
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  db = { imports: [] };
  holding = false;
  held.length = 0;
  await page().evaluate(() => {
    closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    go("home");
  });
});

const sent = (from, methods = WRITES) => app.calls.slice(from).filter((c) => methods.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const dialog = () => page().locator("#dialog-root .dialog");
const importBtn = () => dialog().getByRole("button", { name: "Import", exact: true });
const dayFirst = () => dialog().getByRole("button", { name: "Day first: 30/04/2026" });
async function toastText() {
  const handle = await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; });
  return handle.jsonValue();
}
async function heldCount(n) {
  for (const end = Date.now() + 5000; held.length < n; await pause(20)) if (Date.now() > end) throw new Error(`${held.length} of ${n} previews came`);
}
async function importEnabled() {
  await page().waitForFunction(() => !document.querySelector("#dialog-root .dialog .tool.primary").disabled);
}

async function openImport() {
  await page().evaluate(() => { optimizePage.tab = "cost"; go("optimize"); });
  await page().locator(".usage-card").waitFor();
  await page().getByRole("button", { name: "Import usage file" }).click();
  await dialog().getByLabel("Times are in").waitFor();
}
async function pickFile() {
  await dialog().getByLabel("File").setInputFiles({ name: "metrics.csv", mimeType: "text/csv", buffer: Buffer.from(CSV) });
}

const tzField = () => dialog().getByLabel("Times are in");
async function setTz(value) {
  await tzField().fill(value);
  await tzField().press("Tab");  // the field's change starts a preview
}
async function readyPreview() {  // the file picked and its date order chosen, with Times are in at UTC
  await pickFile();
  await dayFirst().click();  // the first look asks for the date order
  await dialog().locator("text=Recognized as").waitFor();
  await importEnabled();
}

test("an older preview that isn't ready, answering after the newer ready one, is ignored: Import stays enabled and sends the newer options", { skip }, async () => {
  await openImport();
  await readyPreview();
  holding = true;
  const from = app.calls.length;
  await setTz("+2:0");  // a typo: this preview won't be ready
  await setTz("+02:00");  // fixed while the first is still out
  await heldCount(2);
  assert.deepStrictEqual(held.map((x) => x.options), [{ tz: "+2:0", date_order: "dmy" }, { tz: "+02:00", date_order: "dmy" }]);
  holding = false;
  release(1);  // the newer preview answers first
  await dialog().locator("text=Recognized as").waitFor();
  await importEnabled();
  release(0);  // then the older one, which says the time zone can't be read
  await pause(300);
  assert.strictEqual(await dialog().locator("text=Recognized as").count(), 1, "the newer preview is still shown");
  assert.doesNotMatch(await dialog().textContent(), /isn't a time zone offset/, "the older preview's problem isn't shown");
  assert.ok(!(await importBtn().isDisabled()), "Import stays enabled");
  assert.deepStrictEqual(sent(from), [], "the previews save nothing");
  await importBtn().click();
  await dialog().waitFor({ state: "detached" });
  assert.match(await toastText(), /^Imported 2 rows of measured usage/);
  assert.deepStrictEqual(sent(from), [{ method: "usage_import", params: { file_name: "metrics.csv", content: CSV, options: { tz: "+02:00", date_order: "dmy" } } }]);
});

test("two ready previews answering out of order: Import sends the options of the newer one, the one shown", { skip }, async () => {
  await openImport();
  await readyPreview();
  holding = true;
  const from = app.calls.length;
  await setTz("+05:30");
  await setTz("+02:00");
  await heldCount(2);
  holding = false;
  release(1);
  await dialog().locator("text=Recognized as").waitFor();
  await importEnabled();
  release(0);
  await pause(300);
  await importBtn().click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(sent(from), [{ method: "usage_import", params: { file_name: "metrics.csv", content: CSV, options: { tz: "+02:00", date_order: "dmy" } } }]);
});

test("changing Times are in while the file's first preview is out: the older preview is ignored, and Import sends the newer time zone", { skip }, async () => {
  await openImport();
  holding = true;
  const from = app.calls.length;
  await pickFile();
  await heldCount(1);
  await setTz("+05:30");
  await heldCount(2);
  assert.deepStrictEqual(held.map((x) => x.options), [{ tz: "UTC" }, { tz: "+05:30" }]);
  holding = false;
  release(1);
  await dialog().getByRole("button", { name: "Month first: 04/30/2026" }).waitFor();
  release(0);
  await pause(300);
  await dialog().getByRole("button", { name: "Month first: 04/30/2026" }).click();
  await dialog().locator("text=Recognized as").waitFor();
  await importEnabled();
  await importBtn().click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(sent(from), [{ method: "usage_import", params: { file_name: "metrics.csv", content: CSV, options: { tz: "+05:30", date_order: "mdy" } } }]);
});

test("opening the dialog and previewing a file sends no write", { skip }, async () => {
  const from = app.calls.length;
  await openImport();
  await pickFile();
  await dayFirst().click();
  await dialog().locator("text=Recognized as").waitFor();
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await pause(300);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of READS) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
  assert.strictEqual(made.filter((m) => LOOKUPS.includes(m)).length, 2);
  // Anything else is the app's own polling (the approval polls repeat), never a new kind of call.
  assert.deepStrictEqual([...new Set(made.filter((m) => !READS.includes(m) && !LOOKUPS.includes(m) && !baseline.has(m)))], []);
});

test("axe finds nothing serious on the import dialog and its preview", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  const check = async (where) => assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), [], where);
  await openImport();
  await pickFile();
  await dayFirst().waitFor();
  await check("asking the date order");
  await dayFirst().click();
  await dialog().locator("text=Recognized as").waitFor();
  await check("ready to import");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

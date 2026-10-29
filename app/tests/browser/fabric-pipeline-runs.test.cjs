// Monitor > Runs lists pipeline runs in Fabric (Data Factory DF-1, Preview): a background scan that
// says it's reading, rows that open to their activities read from Fabric, and a run this app
// started listed once (see harness.cjs).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
// The scan stays pending until the first test has seen Runs say so: Monitor can be drawn more than once
// on a slow start, so "the first call is pending" isn't enough.
let scanDone = false;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const WS = "11111111-2222-3333-4444-555555555555";
const RUN = (id, name, state, extra = {}) => ({ where: "fabric", workspace_id: WS, workspace_name: "Sales Dev", item_id: `item-${id}`,
  name, job_id: id, state, native_status: state, started: "2026-10-24T06:00:00", ended: "2026-10-24T06:04:12",
  seconds: 252, invoke: "Scheduled", message: "", ...extra });
const ACTIVITIES = [
  { name: "Copy customers", type: "Copy", state: "failed", started: "2026-10-24T06:00:14Z", ms: 221000, retry: null,
    input: { text: "{\n  \"source\": \"erp\"\n}", truncated: false }, output: { text: "", truncated: false },
    error: { code: "SqlFailedToConnect", message: "Cannot connect to SQL Database 'erp'.", failure_type: "UserError" } },
  { name: "Get watermark", type: "Lookup", state: "succeeded", started: "2026-10-24T06:00:00Z", ms: 14000, retry: null,
    input: { text: "", truncated: false }, output: { text: "{\"firstRow\": {}}", truncated: false }, error: null },
  { name: "Merge to silver", type: "TridentNotebook", state: "skipped", started: null, ms: null, retry: null,
    input: { text: "", truncated: false }, output: { text: "", truncated: false }, error: null },
];

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      history: () => [], meta_runs_all: () => ({ runs: [] }), pc_schedule_runs: () => [], pipeline_runs: () => ({ runs: [] }),
      fabric_runs: () => [{ name: "pl_ours", kind: "Pipeline", status: "Completed", job_id: "j-ours", started_at: "2026-10-24T05:00:00Z",
        finished_at: "2026-10-24T05:01:00Z", workspace_name: "Sales Dev" }],
      fabric_pipeline_runs: (p) => {
        if (!scanDone) return { pending: true, runs: [] };
        // Until Refresh, the scan is 20 minutes old: a run read as going then is Stale now.
        const scanned = new Date(Date.now() - (p.refresh ? 0 : 20 * 60000)).toISOString();
        return { pending: false, missed: 1, scanned, runs: [RUN("j-1", "pl_customer_load", "failed", { message: "Copy customers failed" }),
          RUN("j-2", "pl_refresh_models", "succeeded", { started: "2026-10-24T05:30:00" }), RUN("j-ours", "pl_ours", "succeeded"),
          RUN("j-3", "pl_long", "running", { started: "2026-10-24T04:00:00", ended: null, seconds: null }),
          RUN("j-4", "pl_secret", "succeeded", { started: "2026-10-24T03:00:00" }),
          RUN("j-5", "cj_erp_customers", "succeeded", { kind: "copy job", started: "2026-10-24T02:00:00" }),
          RUN("j-6", "df_products", "failed", { kind: "dataflow", started: "2026-10-24T01:00:00", message: "Credentials expired" })] };
      },
      fabric_pipeline_activity_runs: (p) => {
        if (p.job_id === "j-4") throw new Error("POST https://api.fabric.microsoft.com/... failed with 403: {}");
        return { activities: ACTIVITIES };
      },
    },
  });
  app.errors.length = 0;
  await page().evaluate(() => { monitorPage.panel = "runs"; go("monitor"); });
});
after(async () => { if (app) await app.close(); });

test("Runs says it's reading, then lists Fabric pipeline runs once each", { skip }, async () => {
  await page().locator(".monitor-note", { hasText: "Reading pipeline, copy job and dataflow runs in Fabric" }).waitFor();
  // While the scan runs, only the scan is asked again: the other sources wait for the redraw.
  const count = (m) => app.calls.filter((c) => c.method === m).length;
  const others = count("meta_runs_all");
  const polls = count("fabric_pipeline_runs");
  for (let i = 0; i < 80 && count("fabric_pipeline_runs") === polls; i++) await page().waitForTimeout(50);
  assert.ok(count("fabric_pipeline_runs") > polls, "the scan is asked again");
  assert.strictEqual(count("meta_runs_all"), others, "Runs isn't redrawn while the scan is pending");
  scanDone = true;
  await page().locator("tr.pl-run", { hasText: "pl_customer_load" }).waitFor({ timeout: 10000 });
  const texts = await page().locator("tr.pl-run").allTextContents();
  assert.strictEqual(texts.length, 4, texts.join("\n"));  // pl_ours is listed from fabric_runs, not again
  assert.ok(texts.every((t) => /Pipeline run in Fabric · scheduled · as of \d/.test(t) && /Fabric · Sales Dev/.test(t)), texts.join("\n"));
  assert.match(texts[0], /^Failed/);
  // Problems first: the real failure, then the run whose status is too old to trust, then the rest.
  assert.match(texts[1], /^Stale.*pl_long.*—/);
  assert.match(texts[0], /Copy customers failed/);
  assert.match(await page().locator(".monitor-note").textContent(), /Runs of 1 item in Fabric couldn't be read/);
  assert.strictEqual(await page().locator("tr", { hasText: "pl_ours" }).count(), 1);
  // Copy job and dataflow runs are listed too, by their kind; only pipeline runs open to activities.
  assert.match(await page().locator("tr", { hasText: "cj_erp_customers" }).textContent(), /Copy job run in Fabric · scheduled/);
  const dataflow = page().locator("tr", { hasText: "df_products" });
  assert.match(await dataflow.textContent(), /^Failed.*Dataflow refresh in Fabric.*Credentials expired/);
  assert.strictEqual(await dataflow.locator(".pl-toggle").count(), 0);
  await page().evaluate(() => { window.realOpenInFabric = window.openInFabric; window.openInFabric = (url) => { window.openedUrl = url; }; });
  await dataflow.click();
  assert.match(await page().evaluate(() => window.openedUrl), new RegExp(`/groups/${WS}/`));  // the workspace, until the page address is known
  await page().evaluate(() => { window.openInFabric = window.realOpenInFabric; });
});

test("a Fabric run opens to its activities, oldest first, with the error and input on request", { skip }, async () => {
  const row = page().locator("tr.pl-run", { hasText: "pl_customer_load" });
  await row.locator(".pl-toggle").click();
  await page().locator("tr.pl-child").nth(2).waitFor();
  const call = app.calls.find((c) => c.method === "fabric_pipeline_activity_runs").params;
  assert.deepStrictEqual(call, { workspace_id: WS, job_id: "j-1", started: "2026-10-24T06:00:00", ended: "2026-10-24T06:04:12" });
  const kids = await page().locator("tr.pl-child").allTextContents();
  assert.match(kids[0], /^SkippedMerge to silver · Notebook/);  // no start time sorts first
  assert.match(kids[1], /^SucceededGet watermark · Lookup/);
  assert.match(kids[2], /^FailedCopy customers · Copy data/);
  assert.match(kids[2], /SqlFailedToConnect: Cannot connect to SQL Database 'erp'\./);
  assert.match(await row.textContent(), /2 of 3 activities ran/);
  await page().locator("tr.pl-child details.pl-io summary", { hasText: "Input" }).click();
  assert.match(await page().locator("tr.pl-child details.pl-io[open] pre").textContent(), /"source": "erp"/);
  if (SHOTS) await page().locator(".monitor-local").screenshot({ path: path.join(SHOTS, "monitor-fabric-pipeline-runs.png") });
  await row.click();
  assert.strictEqual(await page().locator("tr.pl-child").count(), 0);
});

test("a redraw reuses the activities read; a 403 says not authorized, never an empty list", { skip }, async () => {
  const reads = () => app.calls.filter((c) => c.method === "fabric_pipeline_activity_runs").length;
  const row = page().locator("tr.pl-run", { hasText: "pl_customer_load" });
  await row.locator(".pl-toggle").click();
  await page().locator("tr.pl-child").nth(2).waitFor();
  const before = reads();
  await page().evaluate(() => render());
  await page().locator("tr.pl-child").nth(2).waitFor();
  assert.strictEqual(reads(), before, "drawing Runs again doesn't ask Fabric again");
  await page().locator("tr.pl-run", { hasText: "pl_customer_load" }).click();
  await page().locator("tr.pl-run", { hasText: "pl_secret" }).click();
  await page().locator("tr.pl-child", { hasText: "Not authorized to read activities" }).waitFor();
  await page().locator("tr.pl-run", { hasText: "pl_secret" }).click();
});

test("Open in Fabric goes to the pipeline, and Refresh asks for a new scan", { skip }, async () => {
  await page().evaluate(() => { window.openInFabric = (url) => { window.openedUrl = url; }; });
  await page().locator("tr.pl-run", { hasText: "pl_customer_load" }).locator("button", { hasText: "Open in Fabric" }).click();
  assert.match(await page().evaluate(() => window.openedUrl), new RegExp(`/groups/${WS}/pipelines/item-j-1`));
  assert.strictEqual(await page().locator("tr.pl-child").count(), 0, "the button doesn't also open the activities");
  await page().locator(".monitor-view-row button", { hasText: "Refresh" }).click();
  for (let i = 0; i < 100 && !app.calls.some((c) => c.method === "fabric_pipeline_runs" && c.params?.refresh === true); i++) {
    await page().waitForTimeout(50);
  }
  assert.ok(app.calls.some((c) => c.method === "fabric_pipeline_runs" && c.params?.refresh === true));
  // A fresh read: the long run shows Running again.
  await page().locator("tr.pl-run", { hasText: "pl_long" }).filter({ hasText: /^Running/ }).waitFor();
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

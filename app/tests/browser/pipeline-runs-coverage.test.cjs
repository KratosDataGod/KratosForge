// Monitor > Runs, pipeline runs (pipeline-runs.js) in the states the other tests don't reach: a local run
// that failed, is running, was cancelled, was interrupted or is in a state the app doesn't know; a run
// whose record can't be read or has no activities; activities cancelled before or after they started;
// and Fabric runs that are cancelled, unknown or running, whose activities can't be read, aren't
// returned, are none yet, or were retried with input cut short. Every engine call is mocked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const ago = (min) => new Date(Date.now() - min * 60000).toISOString();

const LOCAL = [
  ["r-failed", "pl_failed", "failed"], ["r-running", "pl_running", "running"], ["r-cancelled", "pl_cancelled", "cancelled"],
  ["r-interrupted", "pl_interrupted", "interrupted"], ["r-odd", "pl_odd", "paused"], ["r-empty", "pl_empty", "succeeded"],
].map(([run_id, pipeline, state], i) => ({ run_id, pipeline, state, started: ago(30 + i), seconds: 12, options: { mocked: [] } }));

// pipeline_run_status per run: a record, null, or an error.
const RECORDS = {
  "r-failed": () => { throw new Error("The run's record is locked by another process."); },
  "r-running": () => ({ state: "running", activities: [] }),
  "r-cancelled": () => ({ state: "cancelled", activities: [
    { id: "a1", name: "Wait a bit", type: "Wait", state: "cancelled", executed_by: "engine", seconds: 3 },
    { id: "a2", name: "Copy orders", type: "Copy", state: "cancelled", executed_by: "not_started", seconds: null },
  ] }),
  "r-odd": () => null,
  "r-empty": () => ({ state: "succeeded", activities: [] }),
};

const WS = "w-sales";
const FRUN = (job_id, name, state, extra = {}) => ({ where: "fabric", workspace_id: WS, workspace_name: "Sales Dev", item_id: `item-${job_id}`,
  name, job_id, state, native_status: state, started: ago(10).replace("Z", ""), ended: null, seconds: 30, invoke: "Manual", message: "", ...extra });
const FABRIC = [
  FRUN("j-cancel", "fab_cancelled", "cancelled"),
  FRUN("j-odd", "fab_odd", "deduped", { native_status: "Deduped" }),
  FRUN("j-run", "fab_running", "running", { seconds: null }),
  FRUN("j-none", "fab_none", "succeeded"),
  FRUN("j-null", "fab_null", "succeeded"),
  FRUN("j-retry", "fab_retry", "succeeded"),
];
const ACTIVITIES = {
  "j-cancel": () => { throw new Error("Fabric took too long to answer."); },
  "j-run": () => ({ activities: [] }),
  "j-none": () => ({ activities: [] }),
  "j-null": () => null,
  "j-retry": () => ({ activities: [{ name: "Copy customers", type: "Copy", state: "succeeded", started: ago(9), ms: 4000, retry: 2,
    input: { text: "{\"source\": \"erp\"}", truncated: true }, output: { text: "", truncated: false }, error: null }] }),
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    history: () => [], meta_runs_all: () => ({ runs: [] }), pc_schedule_runs: () => [], fabric_runs: () => [],
    pipeline_runs: () => ({ runs: JSON.parse(JSON.stringify(LOCAL)) }),
    pipeline_run_status: ({ run_id }) => RECORDS[run_id](),
    fabric_pipeline_runs: () => ({ pending: false, scanned: new Date().toISOString(), runs: JSON.parse(JSON.stringify(FABRIC)) }),
    fabric_pipeline_activity_runs: ({ job_id }) => ACTIVITIES[job_id](),
  } });
  app.errors.length = 0;
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => { pipelineRunsOpen.clear(); forgetFabricActivities(); monitorPage.panel = "runs"; go("home"); go("monitor"); });
  await page().locator("tr.pl-run", { hasText: "fab_retry" }).waitFor();
});

const run = (name) => page().locator("tr.pl-run", { hasText: name });
// Opens a run's activities and returns the text of each row under it once they are read.
async function open(name) {
  const tr = run(name);
  await tr.locator(".pl-toggle").click();
  const id = await tr.getAttribute("data-pl-run");
  const handle = await page().waitForFunction((id) => {
    const kids = [...document.querySelectorAll(`tr.pl-child[data-pl-run="${CSS.escape(id)}"]`)];
    return kids.length && !kids.some((k) => /Loading activities|reading them from Fabric/.test(k.textContent)) && kids.map((k) => k.innerText.trim());
  }, id);
  return handle.jsonValue();
}
const pill = async (name) => (await run(name).locator("td").first().innerText()).trim();

test("local pipeline runs show Failed, Running, Cancelled, Interrupted and Unknown", { skip }, async () => {
  assert.strictEqual(await pill("pl_failed"), "Failed");
  assert.strictEqual(await pill("pl_running"), "Running");
  assert.strictEqual(await pill("pl_cancelled"), "Cancelled");
  assert.strictEqual(await pill("pl_interrupted"), "Interrupted");
  assert.strictEqual(await run("pl_interrupted").locator(".pill-s", { hasText: "Interrupted" }).getAttribute("title"),
    "KratosForge stopped while this run was going. It wasn't resumed.");
  assert.strictEqual(await pill("pl_odd"), "Unknown");
  assert.deepStrictEqual(app.errors, []);
});

test("a local run's activities say when the record can't be read, has none yet, or none at all", { skip }, async () => {
  assert.deepStrictEqual(await open("pl_failed"), ["Activities not loaded: The run's record is locked by another process."]);
  assert.deepStrictEqual(await open("pl_odd"), ["Activities not loaded: this run's record couldn't be read."]);
  assert.deepStrictEqual(await open("pl_running"), ["No activity has started yet. Refresh to see them as they run."]);
  assert.deepStrictEqual(await open("pl_empty"), ["This run has no activity records."]);
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "pipeline_run_status").slice(-1)[0].params, { run_id: "r-empty", values: true });
});

test("a cancelled run's activities say which were cancelled and which never started", { skip }, async () => {
  const kids = await open("pl_cancelled");
  assert.strictEqual(kids.length, 2);
  assert.match(kids[0], /^Cancelled\s+Wait a bit · Wait/);
  assert.match(kids[1], /^Not started\s+Copy orders · Copy data/);
  assert.match(await run("pl_cancelled").innerText(), /1 of 2 activities ran/);
});

test("Fabric runs show Cancelled, Unknown with what Fabric said, and Running when freshly read", { skip }, async () => {
  assert.strictEqual(await pill("fab_cancelled"), "Cancelled");
  assert.strictEqual(await pill("fab_odd"), "Unknown");
  assert.strictEqual(await run("fab_odd").locator(".pill-s", { hasText: "Unknown" }).getAttribute("title"), "Fabric said Deduped");
  assert.strictEqual(await pill("fab_running"), "Running");
  assert.match(await run("fab_running").innerText(), /Pipeline run in Fabric · started by hand · as of/);
});

test("a Fabric run's activities say why they weren't read, that there are none, and show a retry with its input cut short", { skip }, async () => {
  assert.deepStrictEqual(await open("fab_cancelled"), ["Activities not loaded: Fabric took too long to answer. Refresh tries again."]);
  assert.deepStrictEqual(await open("fab_null"), ["Activities not loaded: Fabric didn't return them. Refresh tries again."]);
  assert.deepStrictEqual(await open("fab_running"), ["No activity has started yet. Refresh to see them as they run."]);
  assert.deepStrictEqual(await open("fab_none"), ["Fabric has no activity records for this run."]);
  const [retry] = await open("fab_retry");
  assert.match(retry, /Copy customers · Copy data · retry 2/);
  const row = page().locator("tr.pl-child", { hasText: "Copy customers" });
  await row.locator("summary", { hasText: "Input" }).click();
  await row.getByText("Cut short: open the run in Fabric for all of it.").waitFor();
  assert.strictEqual(await row.locator("details.pl-io pre").innerText(), "{\"source\": \"erp\"}");
  assert.strictEqual(await row.locator("details.pl-io").count(), 1, "an empty output isn't shown");
  // The read is kept: opening the run again doesn't ask Fabric again.
  const asked = app.calls.filter((c) => c.method === "fabric_pipeline_activity_runs").length;
  await run("fab_retry").locator(".pl-toggle").click();
  await row.waitFor({ state: "detached" });
  assert.match((await open("fab_retry"))[0], /retry 2/);
  assert.strictEqual(app.calls.filter((c) => c.method === "fabric_pipeline_activity_runs").length, asked);
});

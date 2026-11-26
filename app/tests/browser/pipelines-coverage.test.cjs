// Pipelines (Preview), the parts the other pipeline tests leave out: the open dialog's repository rows
// and folder choice, the Run dialog's parameters, data steps and notebook choices, a refused start, the
// run view's Stop and activity detail, the Runs and Definition tabs, and the Data Factory commands.
// The real engine reads and checks the pipeline (pipeline_import, pipeline_compatibility,
// pipeline_run_check, pipeline_export); starting, following and stopping a run are mocked, so nothing
// runs and the test needs no notebook or lakehouse.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

// A small pipeline: a Lookup and a Script (data steps), a notebook, a Fabric-only Outlook step and a Wait.
const PIPELINE = JSON.stringify({ name: "Nightly load", properties: {
  parameters: { region: { type: "string", defaultValue: "emea" }, limit: { type: "int", defaultValue: 5 },
    key: { type: "securestring", defaultValue: "s3cret" } },
  activities: [
    { name: "Find files", type: "Lookup", dependsOn: [], typeProperties: { source: { type: "LakehouseTableSource" },
      datasetSettings: { type: "LakehouseTable", linkedService: { name: "lh", properties: { type: "Lakehouse", typeProperties: { artifactId: "lh1" } } },
        typeProperties: { table: "files" } } } },
    { name: "Clean", type: "Script", dependsOn: [{ activity: "Find files", dependencyConditions: ["Succeeded"] }],
      typeProperties: { scripts: [{ type: "Query", text: "SELECT 1" }] },
      linkedService: { name: "lh", properties: { type: "Lakehouse", typeProperties: { artifactId: "lh1" } } } },
    { name: "Transform", type: "TridentNotebook", dependsOn: [{ activity: "Clean", dependencyConditions: ["Succeeded"] }],
      typeProperties: { notebookId: "nb1", workspaceId: "ws1" } },
    { name: "Notify", type: "Office365Outlook", dependsOn: [{ activity: "Transform", dependencyConditions: ["Failed"] }], typeProperties: {} },
    { name: "Pause", type: "Wait", dependsOn: [{ activity: "Transform", dependencyConditions: ["Succeeded"] }], typeProperties: { waitTimeInSeconds: 1 } },
    { name: "Archive", type: "Custom", dependsOn: [{ activity: "Pause", dependencyConditions: ["Succeeded"] }], typeProperties: {} },
  ] } });
const DIR = fs.mkdtempSync(path.join(os.tmpdir(), "pl-cov-"));
const FILE = path.join(DIR, "nightly.json");
fs.writeFileSync(FILE, PIPELINE);
// A .DataPipeline folder as Fabric Git stores it, and a folder that has only its .platform.
const CONTENT = JSON.stringify({ properties: JSON.parse(PIPELINE).properties });
const PLATFORM = JSON.stringify({ metadata: { type: "DataPipeline", displayName: "Nightly load" } });
const FOLDER = path.join(DIR, "Nightly.DataPipeline");
const HALF = path.join(DIR, "Half.DataPipeline");
fs.mkdirSync(FOLDER);
fs.mkdirSync(HALF);
fs.writeFileSync(path.join(FOLDER, "pipeline-content.json"), CONTENT);
fs.writeFileSync(path.join(FOLDER, ".platform"), PLATFORM);
fs.writeFileSync(path.join(HALF, ".platform"), PLATFORM);

const RUN = "7f3a9c21-0000-4000-8000-000000000001";
const T0 = "2026-03-02T08:00:00Z";
// The run as pipeline_run_status reads it: running until Stop, then cancelled.
const runRecord = (state) => ({
  run_id: RUN, pipeline: "Nightly load", state, started: T0, ended: state === "running" ? null : "2026-03-02T08:00:09Z",
  seconds: state === "running" ? null : 9, parameters: { region: "apac", limit: 7 }, options: { mocked: ["Notify"] },
  problems: state === "running" ? [] : [{ message: "Stopped by you." }], notes: ["Notebook steps ran in a sandbox."],
  activities: [
    { id: "a1", name: "Find files", type: "Lookup", state: "succeeded", started: T0, ended: "2026-03-02T08:00:01Z", seconds: 0.05, output: { count: 2 } },
    { id: "a2", name: "Clean", type: "Script", state: "failed", started: "2026-03-02T08:00:01Z", ended: "2026-03-02T08:00:03Z", seconds: 2, attempts: 2,
      error: { message: "Invalid object name 'files'." }, output: undefined },
    { id: "a3", name: "Transform", type: "TridentNotebook", state: "running", started: "2026-03-02T08:00:03Z", ended: null, seconds: null },
  ],
});

// pipeline_run_check as the engine answers it for this pipeline (codes and messages as the real engine
// gives them), except that the notebook and the local data project the dialog names exist here.
const AT = { "Find files": 0, Clean: 1, Transform: 2, Notify: 3, Pause: 4, Archive: 5 };
function runCheck(p) {
  const problems = [];
  const add = (activity, code, message) => problems.push({ code, message, activity, path: `$.properties.activities[${AT[activity]}]` });
  const mocks = p.mocks || {};
  for (const a of ["Find files", "Clean"]) {
    if (mocks[a] || (a === "Clean" && p.sql_projects?.Clean)) continue;
    if (!p.read_lakehouses) add(a, "data_needs_engine", `${a} reads a lakehouse; allow lakehouse reads or give a mock.`);
    else if (!p.workspace_id) add(a, "data_workspace_unknown", `${a}'s lakehouse doesn't name its workspace (it is the pipeline's own). Say which workspace the pipeline is in (workspace_id), or give a mock.`);
  }
  if (!mocks.Transform && !p.notebooks?.Transform) add("Transform", "notebook_unmapped", "Transform runs notebook nb1; say which notebook on this PC it is, or give a mock for it.");
  if (!mocks.Notify) add("Notify", "not_local", "Notify (Office365Outlook) only runs in Fabric. Give a mock for it, or run the pipeline in Fabric.");
  if (!mocks.Archive) add("Archive", "not_local", "Archive (Custom) only runs in Fabric. Give a mock for it, or run the pipeline in Fabric.");
  return { ok: !problems.length, problems, pipeline: "Nightly load", mocked: Object.keys(mocks), fabric_differences: [] };
}

before(async () => {
  if (skip) return;
  const runs = [];
  let stopped = false;
  let refuse = true;
  app = await openApp({
    engine: ["pipeline_import", "pipeline_compatibility", "pipeline_export"],
    mocks: {
      readiness_check: () => [],
      pipeline_list: () => ({ pipelines: [
        { name: "Nightly load", path: FILE, repo: "sales-etl", relative: "nightly.json", activity_count: 5, last_run: null },
        { name: "Hourly", path: "/r/h.json", repo: "sales-etl", relative: "h.json", activity_count: 3, last_run: { state: "succeeded" } },
        { name: "Weekly", path: "/r/w.json", repo: "sales-etl", relative: "w.json", activity_count: 2, last_run: { state: "running" } },
        { name: "Monthly", path: "/r/m.json", repo: "sales-etl", relative: "m.json", activity_count: 4, last_run: { state: "failed" } },
        { name: "Broken", path: "/r/b.json", repo: "sales-etl", relative: "b.json", problem: "bad json" },
      ] }),
      list_notebooks: () => [{ name: "Transform orders" }, { name: "Scratch" }],
      list_local_projects: () => [{ id: "p1", name: "Sales DW" }],
      list_workspaces: () => [{ id: "ws-a", displayName: "Sales Dev" }, { id: "ws-b", displayName: "Sales Prod" }],
      workspace_pipelines: (p) => ({ workspace_id: p.workspace_id, pipelines: [] }),
      pipeline_run_check: runCheck,
      pipeline_runs: () => ({ runs }),
      pipeline_run_start: () => {
        if (refuse) { refuse = false; return { ok: false, problems: [{ message: "Transform: the notebook Scratch isn't saved here." }] }; }
        runs.push({ run_id: RUN, pipeline: "Nightly load", state: "failed", started: T0, seconds: 9, counts: { succeeded: 1, failed: 1 },
          options: { mocked: ["Notify"] } });
        return { ok: true, run_id: RUN };
      },
      pipeline_run_status: () => runRecord(stopped ? "cancelled" : "running"),
      pipeline_run_cancel: () => { const was = stopped; stopped = true; return { cancelled: !was }; },
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof pipelineOpenDialog === "function" && !!document.querySelector(".rail-item"));
  await page().evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copied = t; } } });
    // The Run dialog's checks, kept once the engine has answered them.
    const call = window.__fdCall;
    window.__checks = [];
    window.__fdCall = async (method, params) => {
      const out = await call(method, params);
      if (method === "pipeline_run_check") window.__checks.push(params);
      return out;
    };
  });
  app.errors.length = 0;
});
after(async () => {
  if (app) await app.close();
  fs.rmSync(DIR, { recursive: true, force: true });
});

const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.querySelector("#toast")?.textContent || ""), re.source);
const dialog = () => page().locator(".pl-run-dialog");

test("the open dialog lists repository pipelines with their last run, and signed out offers sign-in", { skip }, async () => {
  await page().evaluate(() => { state.user = null; plPage.pipeline = null; plPage.area = "pipelines"; go("pipelines"); });
  await page().locator(".page button", { hasText: "Open a pipeline" }).click();
  await page().locator(".pl-open-row", { hasText: "Broken" }).waitFor();
  const rows = await page().locator(".pl-open-list .pl-open-row").allTextContents();
  assert.deepStrictEqual(rows, [
    "Nightly load5 activities · sales-etlNever run here",
    "Hourly3 activities · sales-etlLast run succeeded",
    "Weekly2 activities · sales-etlRunning",
    "Monthly4 activities · sales-etlLast run failed",
    "BrokenCan't be readNever run here",
  ]);
  assert.ok(await page().locator(".pl-open-fabric button", { hasText: "Sign in to list workspace pipelines" }).isVisible());
  assert.ok(!app.calls.some((c) => c.method === "list_workspaces"), "signed out, workspaces aren't listed until asked");
});

test("a folder without pipeline-content.json says so; the .DataPipeline folder opens", { skip }, async () => {
  const folder = 'input[aria-label="Choose a .DataPipeline folder"]';
  await page().setInputFiles(folder, HALF);
  await page().locator(".pl-open .error-text", { hasText: "That folder has no pipeline-content.json." }).waitFor();
  await page().setInputFiles(folder, FOLDER);
  await page().waitForSelector(".pl-graph");
  // Both parts went to the engine as base64, as Fabric stores them.
  const sent = JSON.parse(app.calls.filter((c) => c.method === "pipeline_import").at(-1).params.text);
  assert.deepStrictEqual(sent.parts.map((x) => [x.path, x.payloadType, Buffer.from(x.payload, "base64").toString()]),
    [["pipeline-content.json", "InlineBase64", CONTENT], [".platform", "InlineBase64", PLATFORM]]);
  assert.match(await page().textContent(".pl-head"), /From Nightly\.DataPipeline · read-only here/);
});

test("a repository row opens its pipeline: the summary, a Fabric-only step and its mock button", { skip }, async () => {
  await page().evaluate(() => pipelineOpenDialog());
  await page().locator(".pl-open-row", { hasText: "Nightly load" }).click();
  await page().locator(".pl-head", { hasText: "From sales-etl · nightly.json" }).waitFor();
  await page().waitForSelector(".pl-summary");
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "pipeline_import").at(-1).params, { path: FILE });
  const summary = await page().textContent(".pl-summary");
  assert.match(summary, /^6 activities: 4 run here1 needs a mock1 Fabric only · Parameters: region \(string\), limit \(int\), key \(securestring\)$/);
  await page().locator(".pl-node", { hasText: "Archive" }).click();
  await page().waitForSelector(".pl-detail");
  assert.match(await page().textContent(".pl-detail"),
    /^ArchiveFabric onlyActivity type this version doesn't knowActivity type this version doesn't know runs only in Fabric\. Give it a mock to run the rest here\./);
  await page().locator(".pl-detail button", { hasText: "Set its mock output" }).click();
  await dialog().locator(".pl-mock", { hasText: "Archive" }).waitFor();
  assert.deepStrictEqual((await dialog().locator(".pl-mock b").allTextContents()).sort(), ["Archive", "Clean", "Find files", "Notify"]);
  await page().locator(".dialog-actions button", { hasText: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
});

test("Runs is empty before a run, and Definition copies pipeline-content.json", { skip }, async () => {
  await page().locator('[role="tab"]', { hasText: "Runs" }).click();
  await page().locator(".pl-tab", { hasText: "No runs here yet" }).waitFor();
  assert.ok(await page().locator(".pl-tab button", { hasText: "Run on this PC" }).isVisible());
  await page().locator('[role="tab"]', { hasText: "Definition" }).click();
  await page().waitForSelector(".pl-json");
  const shown = await page().textContent(".pl-json");
  assert.match(shown, /"name":"Find files"/);
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "pipeline_export").at(-1).params, { path: FILE, format: "pipeline_content" });
  await page().locator(".pl-tab button", { hasText: "Copy" }).click();
  await toastSays(/^Copied the definition\.$/);
  assert.strictEqual(await page().evaluate(() => window.__copied), shown);
  await page().locator('[role="tab"]', { hasText: "Graph" }).click();
  await page().waitForSelector(".pl-graph");
});

test("the Run dialog fills parameters, flags a bad one and checks data, notebook and Script choices", { skip }, async () => {
  await page().locator(".pl-actions button", { hasText: "Run on this PC" }).click();
  await dialog().waitFor();
  assert.strictEqual(await dialog().getByLabel("region", { exact: true }).inputValue(), "emea");
  assert.strictEqual(await dialog().getByLabel("limit", { exact: true }).inputValue(), "5");
  const key = dialog().getByLabel("key", { exact: true });
  assert.strictEqual(await key.inputValue(), "", "a SecureString default is never shown");
  assert.strictEqual(await key.getAttribute("type"), "password");
  // A value that isn't JSON for an int says so and isn't checked.
  const checks = () => app.calls.filter((c) => c.method === "pipeline_run_check").length;
  await page().locator(".pl-check .pill-s").waitFor();
  const n0 = checks();
  await dialog().getByLabel("limit", { exact: true }).fill("lots");
  await dialog().locator(".error-text", { hasText: "Not valid int (write it as JSON)." }).waitFor();
  assert.strictEqual(checks(), n0);
  await dialog().getByLabel("limit", { exact: true }).fill("7");
  await dialog().locator(".error-text", { hasText: "Not valid int" }).waitFor({ state: "hidden" });
  await dialog().getByLabel("region", { exact: true }).fill("apac");
  await waitForCheck((p) => p.parameters?.limit === 7 && p.parameters?.region === "apac");
  // Data steps start with a mock; letting them read lakehouses removes it and asks for the workspace.
  assert.ok(await dialog().locator(".pl-mock", { hasText: "Find files" }).isVisible());
  await dialog().locator(".pl-check-row", { hasText: "Let data steps read lakehouses" }).locator("input").check();
  await dialog().locator(".pl-mock", { hasText: "Find files" }).waitFor({ state: "detached" });
  await dialog().getByLabel("Workspace", { exact: true }).selectOption({ label: "Sales Prod" });
  await waitForCheck((p) => p.read_lakehouses === true && p.workspace_id === "ws-b");
  // The Script runs in a local data project.
  await dialog().getByLabel("Clean runs in").selectOption({ label: "Local data project: Sales DW" });
  await waitForCheck((p) => p.sql_projects?.Clean === "p1");
  // The notebook step: a mock first, then a saved notebook.
  const nb = dialog().getByLabel("Transform runs notebook");
  await nb.selectOption({ label: "Give it a mock instead" });
  await dialog().locator(".pl-mock", { hasText: "Transform" }).waitFor();
  await nb.selectOption({ label: "Scratch" });
  await dialog().locator(".pl-mock", { hasText: "Transform" }).waitFor({ state: "detached" });
  await waitForCheck((p) => p.notebooks?.Transform === "Scratch" && p.mocks && !p.mocks.Transform);
  // Notify's mock ends Failed, with output.
  await dialog().getByLabel("Notify ends").selectOption("Failed");
  await dialog().getByLabel("Notify output").fill("{oops");
  await dialog().locator(".error-text", { hasText: "Write the output as JSON." }).waitFor();
  await dialog().getByLabel("Notify output").fill('{"sent": false}');
  await waitForCheck((p) => p.mocks?.Notify?.status === "Failed" && p.mocks?.Notify?.output?.sent === false);
});

test("Run sends every choice; a refused start says why and a started one opens the run view", { skip }, async () => {
  const run = page().locator(".dialog-actions button", { hasText: /^Run$/ });
  await page().waitForFunction(() => !document.querySelector(".pl-check .loading"));
  await dialog().getByLabel("key", { exact: true }).fill("hunter2");
  await waitForCheck((p) => p.parameters?.key === "hunter2");
  await page().waitForFunction(() => {
    const b = [...document.querySelectorAll(".dialog-actions button")].find((x) => x.textContent.trim() === "Run");
    return b && !b.disabled;
  }).catch(async () => { throw new Error(await dialog().locator(".pl-check").innerText()); });
  await run.click();
  await dialog().locator(".error-text", { hasText: "Transform: the notebook Scratch isn't saved here." }).waitFor();
  assert.ok(await run.isEnabled(), "a refused start can be tried again");
  await run.click();
  await page().locator(".pl-head h1", { hasText: "Nightly load" }).waitFor();
  await dialog().waitFor({ state: "detached" });
  const starts = app.calls.filter((c) => c.method === "pipeline_run_start").map((c) => c.params);
  assert.strictEqual(starts.length, 2);
  const sent = starts[1];
  assert.deepStrictEqual(sent.parameters, { region: "apac", limit: 7, key: "hunter2" });
  assert.deepStrictEqual(sent.notebooks, { Transform: "Scratch" });
  assert.deepStrictEqual(sent.sql_projects, { Clean: "p1" });
  assert.strictEqual(sent.read_lakehouses, true);
  assert.strictEqual(sent.workspace_id, "ws-b");
  assert.deepStrictEqual(sent.mocks.Notify, { status: "Failed", output: { sent: false } });
  assert.deepStrictEqual(Object.keys(sent.mocks).sort(), ["Archive", "Notify"]);
  assert.strictEqual(sent.path, FILE);
  // Kept for next time without the SecureString value and without leave to reach the network.
  const kept = await page().evaluate(() => JSON.parse(localStorage.getItem("fd.pipeline.Nightly load")));
  assert.deepStrictEqual(kept.parameters, { region: "apac", limit: 7 });
  assert.strictEqual(kept.allow_uncontained, false);
});

test("the run view shows the running run, an activity's error, and Stop cancels it", { skip }, async () => {
  const head = await page().locator(".pl-head").innerText();
  assert.match(head, /Nightly load\s*Running/);
  assert.match(head, /Run 7f3a9c21 on this PC · .*region = "apac", limit = 7 · 1 mock/);
  const rows = await page().locator(".pl-timeline .pl-row:not(.head)").allInnerTexts();
  assert.deepStrictEqual(rows.map((r) => r.replace(/\s+/g, " ").trim()), [
    "Find files Succeeded <0.1s", "Clean Failed 2s", "Transform Running —"]);
  assert.ok(await page().locator(".pl-actions button", { hasText: "Run again with the same settings" }).isDisabled(), "not while it runs");
  // An activity's detail: its error and attempts; a running one's output comes later.
  await page().locator(".pl-timeline .pl-row", { hasText: "Clean" }).click();
  await page().locator(".pl-detail h2", { hasText: "Clean" }).waitFor();
  const clean = await page().locator(".pl-detail").innerText();
  assert.match(clean, /Script · 2 attempts · 2s/);
  assert.match(clean, /Error\s*\{\s*"message": "Invalid object name 'files'\."\s*\}\s*Output\s*None\./);
  await page().locator(".pl-timeline .pl-row", { hasText: "Transform" }).click();
  await page().locator(".pl-detail", { hasText: "Appears when the step ends." }).waitFor();
  await page().locator(".pl-timeline .pl-row", { hasText: "Find files" }).click();
  await page().locator(".pl-detail", { hasText: '"count": 2' }).waitFor();
  // Stop cancels the run; the next status read shows it cancelled.
  await page().locator(".pl-actions button", { hasText: "Stop" }).click();
  await toastSays(/^Stopping the run: nothing new starts and running steps stop\.$/);
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "pipeline_run_cancel").map((c) => c.params), [{ run_id: RUN }]);
  await page().locator(".pl-head h1", { hasText: "Cancelled" }).waitFor({ timeout: 10000 });
  assert.strictEqual(await page().locator(".pl-actions button", { hasText: "Stop" }).count(), 0);
  assert.match(await page().locator(".pl-timeline").innerText(), /Stopped by you\.[\s\S]*Notebook steps ran in a sandbox\./);
  assert.deepStrictEqual(app.errors, []);
});

test("Run again opens the dialog with the run's own settings, the secure value included", { skip }, async () => {
  await page().locator(".pl-actions button", { hasText: "Run again with the same settings" }).click();
  await dialog().waitFor();
  assert.strictEqual(await dialog().getByLabel("key", { exact: true }).inputValue(), "hunter2");
  assert.strictEqual(await dialog().getByLabel("limit", { exact: true }).inputValue(), "7");
  assert.strictEqual(await dialog().getByLabel("Transform runs notebook").inputValue(), "Scratch");
  assert.strictEqual(await dialog().getByLabel("Notify ends").inputValue(), "Failed");
  await page().locator(".dialog-actions button", { hasText: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
});

test("the Runs tab lists this pipeline's runs, and a row opens its run", { skip }, async () => {
  await page().locator(".pl-actions button", { hasText: "Back to the pipeline" }).click();
  await page().locator('[role="tab"]', { hasText: "Runs" }).click();
  const row = page().locator(".pl-tab tr.click");
  await row.waitFor();
  assert.match((await row.innerText()).replace(/\s+/g, " "), /^Failed .* 9s 1 succeeded, 1 failed, 1 mocked$/);
  await row.click();
  await page().locator(".pl-head h1", { hasText: "Cancelled" }).waitFor();
  await page().locator(".pl-actions button", { hasText: "Back to the pipeline" }).click();
  await page().locator(".pl-tab tr.click").waitFor();
  await page().evaluate(() => { plPage.tab = "graph"; render(); });
});

test("signed in, the open dialog lists the remembered workspace's pipelines", { skip }, async () => {
  await page().evaluate(() => { state.user = "chris@contoso.com"; state.workspaces = null; localStorage.setItem("fd.pipelines.workspace", "ws-b"); pipelineOpenDialog(); });
  await page().locator(".pl-open-fabric", { hasText: "Sales Prod has no pipelines." }).waitFor();
  assert.strictEqual(await page().locator('.pl-open-fabric select[aria-label="Workspace"]').inputValue(), "ws-b");
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "workspace_pipelines").map((c) => c.params), [{ workspace_id: "ws-b" }]);
  await page().selectOption('.pl-open-fabric select[aria-label="Workspace"]', "");
  await page().locator(".pl-open-fabric", { hasText: "Sales Prod has no pipelines." }).waitFor({ state: "detached" });
  await page().locator(".dialog-actions button", { hasText: "Cancel" }).click();
});

for (const [label, area] of [["Data Factory connections", "connections"], ["Copy jobs", "copyjobs"], ["Dataflows", "dataflows"], ["Open a pipeline", null]]) {
  test(`the command "${label}" opens it`, { skip }, async () => {
    await page().evaluate(() => { closeDialog(); go("home"); });
    await page().keyboard.press("Control+k");
    await page().keyboard.type(label);
    await page().locator("#palette .palette-item.on", { hasText: label }).waitFor();
    await page().keyboard.press("Enter");
    if (area) await page().waitForFunction((a) => state.view === "pipelines" && plPage.area === a, area);
    else await page().locator(".pl-open").waitFor();
  });
}
// Waits until the engine has answered a Run dialog check whose params pass `ok` (source text of a
// function of the params), and the dialog shows that answer.
function waitForCheck(ok) {
  return page().waitForFunction((src) => {
    const pass = new Function(`return (${src})`)();
    return (window.__checks || []).some(pass) && !!document.querySelector(".pl-check .pill-s");
  }, ok.toString());
}

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

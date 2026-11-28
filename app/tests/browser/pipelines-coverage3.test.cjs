// Pipelines (Preview), beyond pipelines-coverage and the other pipeline tests: the open dialog when the
// repositories or a workspace's pipelines can't be listed, a file that isn't a pipeline, a check that didn't
// finish and Check again, Runs and Definition when they can't be read, a mock taken off with Run it instead and
// one added back when the check says the step can't run here, and the run view: a child pipeline's steps, a
// step that didn't wait, a run with nothing started, Stop on a run that had already ended, and a run that can't
// be read. The real engine reads and checks the pipeline (pipeline_import, pipeline_compatibility,
// pipeline_run_check, pipeline_export); a failure is put in front of a call in the page, and runs are mocked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

// A Wait, which runs here, and a Custom step, which runs only in Fabric.
const PIPELINE = JSON.stringify({ name: "Small load", properties: { activities: [
  { name: "Pause", type: "Wait", dependsOn: [], typeProperties: { waitTimeInSeconds: 1 } },
  { name: "Archive", type: "Custom", dependsOn: [{ activity: "Pause", dependencyConditions: ["Succeeded"] }], typeProperties: {} },
] } });
const DIR = fs.mkdtempSync(path.join(os.tmpdir(), "pl-cov3-"));
const FILE = path.join(DIR, "small.json");
fs.writeFileSync(FILE, PIPELINE);

const T0 = "2026-03-02T08:00:00Z";
const RUN = "9a1b2c3d-0000-4000-8000-000000000003";
// pipeline_run_status's record: an Execute Pipeline step whose child run had steps of its own.
const record = (state, activities) => ({ run_id: RUN, pipeline: "Small load", state, started: T0, ended: state === "running" ? null : "2026-03-02T08:00:05Z",
  seconds: state === "running" ? null : 5, parameters: {}, options: { mocked: [] }, problems: [], notes: [], activities });
const CHILD = [
  { id: "a1", name: "Call child", type: "ExecutePipeline", state: "succeeded", started: T0, ended: "2026-03-02T08:00:04Z", seconds: 4, wait_on_completion: false,
    child_run: { activities: [{ id: "c1", name: "Child step", type: "Wait", state: "succeeded", started: T0, ended: "2026-03-02T08:00:01Z", seconds: 1 }] } },
];

let status;
before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["pipeline_import", "pipeline_compatibility", "pipeline_export", "pipeline_run_check"],
    mocks: {
      readiness_check: () => [],
      pipeline_list: () => ({ pipelines: [{ name: "Small load", path: FILE, repo: "etl", relative: "small.json", activity_count: 2, last_run: null }] }),
      list_workspaces: () => [{ id: "ws-a", displayName: "Sales Dev" }],
      workspace_pipelines: () => { throw new Error("Fabric didn't answer"); },
      pipeline_runs: () => { throw new Error("The run history can't be read"); },
      pipeline_run_status: () => status(),
      pipeline_run_cancel: () => ({ cancelled: false }),
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof pipelineOpenDialog === "function" && !!document.querySelector(".rail-item"));
  // A failure in front of one call (window.__fail[method]), and an extra problem in the run check's answer.
  await page().evaluate(() => {
    const call = window.__fdCall;
    window.__fail = {};
    window.__extra = null;
    // __fdCall answers { result } or { error } (harness.cjs).
    window.__fdCall = async (method, params) => {
      if (window.__fail[method]) return { error: window.__fail[method] };
      const out = await call(method, params);
      if (method === "pipeline_run_check" && window.__extra && out.result) {
        return { result: { ...out.result, ok: false, problems: [...(out.result.problems || []), window.__extra] } };
      }
      return out;
    };
  });
});
after(async () => {
  if (app) await app.close();
  fs.rmSync(DIR, { recursive: true, force: true });
});
beforeEach(async () => {
  if (skip) return;
  status = () => record("succeeded", CHILD);
  await page().evaluate(() => { window.__fail = {}; window.__extra = null; closeDialog(); toastClose(); });
  app.errors.length = 0;
});

const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.querySelector("#toast")?.textContent || ""), re.source);
const dialog = () => page().locator(".pl-run-dialog");
async function openSmall() {
  await page().evaluate((file) => openPipeline({ path: file }, { label: "etl · small.json" }), FILE);
  await page().waitForSelector(".pl-summary");
}

test("the open dialog says when the repositories or a workspace's pipelines can't be listed", { skip }, async () => {
  await page().evaluate(() => { window.__fail.pipeline_list = "The repositories can't be read"; state.user = "ana@contoso.com"; pipelineOpenDialog(); });
  const d = page().getByRole("dialog", { name: "Open a pipeline" });
  await d.getByText("The repositories can't be read").waitFor();
  await d.getByLabel("Workspace").selectOption("ws-a");
  await d.locator(".pl-open-list", { hasText: "Fabric didn't answer" }).waitFor();
  await d.getByRole("button", { name: "Cancel" }).click();
  await page().evaluate(() => { try { localStorage.removeItem("fd.pipelines.workspace"); } catch {} state.user = null; });
  assert.deepStrictEqual(app.errors, []);
});

test("a file that isn't a pipeline says so, and opens nothing", { skip }, async () => {
  await page().evaluate(() => openPipeline({ text: "{\"hello\": 1}" }, { label: "hello.json" }));
  await toastSays(/^Could not read the pipeline: /);
  assert.strictEqual(await page().locator(".pl-head", { hasText: "hello.json" }).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a check that didn't finish says so, and Check again checks it", { skip }, async () => {
  await page().evaluate(() => { window.__fail.pipeline_compatibility = "The checker stopped"; });
  await page().evaluate((file) => openPipeline({ path: file }, { label: "etl · small.json" }), FILE);
  await page().getByText("The check didn't finish. Try Check again.").waitFor();
  await page().evaluate(() => { window.__fail = {}; toastClose(); });
  await page().getByRole("button", { name: "Check again" }).click();
  await page().waitForSelector(".pl-summary");
  assert.deepStrictEqual(app.errors, []);
});

test("Runs and Definition say why when they can't be read", { skip }, async () => {
  await openSmall();
  await page().locator('[role="tab"]', { hasText: "Runs" }).click();
  await page().locator(".pl-tab", { hasText: "The run history can't be read" }).waitFor();
  await page().evaluate(() => { window.__fail.pipeline_export = "The definition can't be written out"; });
  await page().locator('[role="tab"]', { hasText: "Definition" }).click();
  await page().locator(".pl-tab", { hasText: "The definition can't be written out" }).waitFor();
  await page().evaluate(() => { window.__fail = {}; });
  await page().locator('[role="tab"]', { hasText: "Graph" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("Run it instead takes a mock off a step that runs here; a step the check says can't run here gets one back", { skip }, async () => {
  await openSmall();
  await page().evaluate(() => pipelineRunDialog({ mocks: { Pause: { status: "Succeeded", output: {} } } }));
  const pause = dialog().locator(".pl-mock", { hasText: "Pause" });
  await pause.waitFor();
  await page().evaluate(() => {
    window.__extra = { code: "not_local", activity: "Pause", message: "Pause can't run here this time.", path: "$.properties.activities[0]" };
  });
  await pause.getByRole("button", { name: "Run it instead" }).click();
  await pause.waitFor({ state: "detached" });
  // The next check names Pause, so it gets a mock of its own again.
  await pause.waitFor();
  assert.deepStrictEqual((await dialog().locator(".pl-mock b").allTextContents()).sort(), ["Archive", "Pause"]);
  await page().evaluate(() => { window.__extra = null; });
  await page().locator(".dialog-actions button", { hasText: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("the run view shows a child pipeline's steps and a step that didn't wait; a run with nothing started says so", { skip }, async () => {
  await page().evaluate((id) => { plPage.runId = id; plPage.run = null; go("pipelineRun"); }, RUN);
  await page().locator(".pl-row", { hasText: "Child step" }).waitFor();
  assert.match(await page().locator(".pl-row", { hasText: "Call child" }).innerText(), /didn't wait/);
  const childPad = await page().locator(".pl-row", { hasText: "Child step" }).evaluate((el) => el.style.paddingLeft);
  assert.strictEqual(childPad, "32px");

  status = () => record("succeeded", []);
  await page().evaluate(() => { plPage.run = null; renderPipelineRun(); });
  await page().getByText("Nothing has started yet.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Stop on a run that had already ended says so; a run that can't be read, or no run, goes nowhere", { skip }, async () => {
  status = () => record("running", []);
  await page().evaluate((id) => { plPage.runId = id; plPage.run = null; go("pipelineRun"); }, RUN);
  await page().getByRole("button", { name: "Stop" }).click();
  await toastSays(/^The run had already ended\.$/);
  status = () => record("cancelled", []);
  await page().waitForFunction(() => plPage.run?.state === "cancelled");

  status = () => { throw new Error("No such run"); };
  await page().evaluate(() => { toastClose(); plPage.run = null; plPulse = plPage.runId; renderPipelineRun(); });
  await toastSays(/^Could not read the run/);
  assert.strictEqual(await page().evaluate(() => plPulse), null);
  await page().evaluate(() => { toastClose(); plPage.runId = null; go("pipelineRun"); });
  await page().waitForFunction(() => state.view === "pipelines");
  assert.deepStrictEqual(app.errors, []);
});

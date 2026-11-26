// Data Factory copy jobs (df-copy-jobs.js) beyond df-copy-jobs.test.cjs: no workspaces, the remembered
// workspace listed straight away, a copy job with no ends, tables or runs, runs Fabric won't give, and a
// workspace with no copy jobs or one Fabric won't list. The workspace and copy job calls are mocked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const OPS = "11111111-0000-4000-8000-000000000001";
const EMPTY = "11111111-0000-4000-8000-000000000002";
const LOCKED = "11111111-0000-4000-8000-000000000003";
const WORKSPACES = [{ id: OPS, displayName: "Ops" }, { id: EMPTY, displayName: "Scratch" }, { id: LOCKED, displayName: "Finance" }];
let app;
let workspaces;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    list_workspaces: () => workspaces,
    workspace_copy_jobs: ({ workspace_id }) => {
      if (workspace_id === LOCKED) throw new Error("Fabric answered 403: you can't read this workspace");
      return { workspace_id, copy_jobs: workspace_id === OPS ? [
        { workspace_id, item_id: "cj-new", name: "cj_new", description: "", folder_id: "" },
        { workspace_id, item_id: "cj-hist", name: "cj_history_down", description: "", folder_id: "" }] : [] };
    },
    workspace_copy_job_definition: (p) => ({ workspace_id: OPS, item_id: p.item_id, name: p.name, ok: true, problems: [],
      job_mode: "", timeout: "", source: {}, destination: { type: "", connection_type: "" }, tables: [] }),
    fabric_copy_job_runs: (p) => {
      if (p.item_id === "cj-hist") throw new Error("Fabric answered 429: too many requests");
      return { workspace_id: OPS, item_id: p.item_id, runs: [] };
    },
  } });
  await page().waitForFunction(() => typeof renderDfCopyJobs === "function" && !!document.querySelector(".rail-item"));
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  workspaces = WORKSPACES;
  await page().evaluate(() => {
    try { localStorage.removeItem(DF_WORKSPACE_KEY); } catch { /* none */ }
    Object.assign(dfCopyPage, { workspace: null, list: null, selected: null, detail: null, runs: null, tab: "tables" });
    state.user = "chris@contoso.com"; state.workspaces = null;
  });
});

const body = () => page().locator(".df-copy-body");
const openTab = () => page().evaluate(() => { plPage.area = "copyjobs"; plPage.pipeline = null; go("pipelines"); });

test("with no workspaces the tab says there are none to list", { skip }, async () => {
  workspaces = [];
  await openTab();
  await body().getByText("No workspaces to list.").waitFor();
  assert.strictEqual(await body().getByLabel("Workspace").count(), 0);
});

test("the remembered workspace is listed straight away; a copy job with nothing set says so on both tabs", { skip }, async () => {
  await page().evaluate((id) => localStorage.setItem(DF_WORKSPACE_KEY, id), OPS);
  await openTab();
  await page().locator(".df-flow-row", { hasText: "cj_new" }).click();
  await body().getByText("No tables in this copy job.").waitFor();
  assert.strictEqual(await body().getByLabel("Workspace").inputValue(), OPS);
  assert.strictEqual(await page().locator(".df-kv").textContent(),
    "SourceNot setDestinationNot setMode—Timeout—ConnectionsNone (OneLake only)");
  assert.strictEqual(await page().locator(".df-detail-head .pill-s").count(), 0, "no local check, no pill");
  await body().getByRole("tab", { name: "Run history" }).click();
  await body().getByText("No runs in Fabric's history.").waitFor();
});

test("runs Fabric won't give say why on Run history, under the copy job's summary", { skip }, async () => {
  await openTab();
  await body().getByLabel("Workspace").selectOption({ label: "Ops" });
  await page().locator(".df-flow-row", { hasText: "cj_history_down" }).click();
  await page().locator(".df-kv").waitFor();
  await body().getByRole("tab", { name: "Run history" }).click();
  assert.match(await body().locator(".error-text").textContent(), /^Could not read runs: /);
  assert.strictEqual(await page().locator(".df-kv").count(), 1, "the definition was read, so its summary stays");
  assert.strictEqual(await page().evaluate(() => localStorage.getItem(DF_WORKSPACE_KEY)), OPS);
});

test("a workspace with no copy jobs says so, and one Fabric won't list says why", { skip }, async () => {
  await openTab();
  await body().getByLabel("Workspace").selectOption({ label: "Scratch" });
  await body().getByText("Scratch has no copy jobs.").waitFor();
  await body().getByLabel("Workspace").selectOption({ label: "Finance" });
  await body().locator(".error-text", { hasText: /^Could not list copy jobs: / }).waitFor();
  assert.strictEqual(await page().locator(".df-flow-row").count(), 0);
});

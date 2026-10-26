// Notebook Runs panel (notebook parity L3): a notebook's recent runs on this PC and in Fabric. The
// empty state comes from the real engine (a fresh app folder has no runs); the list from mocks.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const CELLS = [{ type: "code", cell_id: "c1", source: "x = 1", meta: {} }];

const ago = (minutes) => new Date(Date.now() - minutes * 60000).toISOString();
const RUNS = [
  { source: "fabric", started: ago(5), ended: null, seconds: null, status: "InProgress", trigger: "fabric", message: "",
    workspace_name: "Sales", url: "https://app.fabric.microsoft.com/groups/ws1/synapsenotebooks/it1", job_id: "j1" },
  { source: "pc", started: ago(60), ended: ago(59), seconds: 42.2, status: "failed", trigger: "schedule",
    message: "Cell 2 of 3 failed: NameError: name 'later' is not defined", alert_error: "Teams said 400", cells: 2, schedule_id: "s1" },
  { source: "pc", started: ago(120), ended: ago(119), seconds: 3.1, status: "ok", trigger: "manual", message: "3 cells ran", cells: 3, schedule_id: "s1" },
];

async function openNotebook(app) {
  await app.page.evaluate((cells) => { loadIntoState({ name: "Daily sales", cells }); go("notebook"); }, CELLS);
  await app.page.getByRole("button", { name: "Schedule" }).click();
  await app.page.getByRole("menuitemcheckbox", { name: /^Recent runs/ }).click();
  return app.page.locator(".runspane");
}

let empty, listed;
let refreshed = [];

before(async () => {
  if (skip) return;
  empty = await openApp({ engine: ["notebook_runs"] });
  listed = await openApp({
    mocks: {
      notebook_runs: (p) => {
        if (!p.refresh) return RUNS;
        refreshed.push(p);
        return [{ ...RUNS[0], status: "Completed", ended: ago(1), seconds: 240 }, ...RUNS.slice(1)];
      },
    },
  });
});

after(async () => {
  if (empty) await empty.close();
  if (listed) await listed.close();
});

test("with no runs, the panel says how to schedule the notebook or run it in Fabric", { skip }, async () => {
  const pane = await openNotebook(empty);
  await pane.getByText("No runs yet.").waitFor();
  const text = await pane.innerText();
  assert.match(text, /Schedule \(Home or Run tab\) runs the saved notebook at set times/);
  assert.match(text, /set Runs on to Fabric and press Run all/);
  assert.match(text, /saves the answer in fabric_runs\.jsonl on this PC/, "How this works names the one write");
  assert.strictEqual(await pane.getByRole("button", { name: "Refresh status" }).count(), 0);
  const call = empty.calls.find((c) => c.method === "notebook_runs");
  assert.deepStrictEqual(call.params, { name: "Daily sales", limit: 50, refresh: false }, "opening asks Fabric nothing");
  if (SHOTS) await empty.page.screenshot({ path: path.join(SHOTS, "notebook-runs-empty.png") });
  await pane.getByRole("button", { name: /^Schedule/ }).click();
  await empty.page.getByText("Schedule Daily sales").first().waitFor();
  assert.deepStrictEqual(empty.errors, []);
});

test("each run says when, where, what started it, its status and time; PC runs open their details", { skip }, async () => {
  const pane = await openNotebook(listed);
  await pane.getByText("3 runs, 1 failed, 1 still going").waitFor();
  const rows = pane.locator(".nbrun");
  assert.strictEqual(await rows.count(), 3);
  assert.match(await rows.nth(0).innerText(), /Running[\s\S]*Run in Fabric[\s\S]*Fabric: Sales/);
  assert.match(await rows.nth(1).innerText(), /Failed[\s\S]*Schedule[\s\S]*This PC · 42s[\s\S]*Cell 2 of 3 failed/);
  assert.match(await rows.nth(2).innerText(), /Succeeded[\s\S]*Schedule, Run now[\s\S]*This PC · 3\.1s/);
  await rows.nth(1).getByRole("button", { name: "Details" }).click();
  const detail = await pane.locator(".nbrun-detail").innerText();
  assert.match(detail, /Cells run\s+2/);
  assert.match(detail, /Teams alert\s+Teams said 400/);
  if (SHOTS) await listed.page.screenshot({ path: path.join(SHOTS, "notebook-runs-list.png") });
  // The Fabric run opens its page in Fabric.
  await rows.nth(0).getByRole("button", { name: "Open in Fabric" }).click();
  assert.strictEqual(await listed.page.evaluate(() => fabricPage.page), RUNS[0].url);
  assert.deepStrictEqual(listed.errors, []);
});

test("Refresh status asks for the Fabric run's status and keeps details open", { skip }, async () => {
  const pane = await openNotebook(listed);
  await pane.locator(".nbrun").first().waitFor();
  await pane.locator(".nbrun").nth(1).getByRole("button", { name: "Details" }).click();
  await pane.getByRole("button", { name: "Refresh status" }).click();
  await pane.getByText("3 runs, 1 failed").waitFor();
  assert.deepStrictEqual(refreshed, [{ name: "Daily sales", limit: 50, refresh: true }]);
  assert.match(await pane.locator(".nbrun").first().innerText(), /Succeeded[\s\S]*Fabric: Sales · 4m 0s/);
  assert.strictEqual(await pane.getByRole("button", { name: "Refresh status" }).count(), 0, "nothing left to ask about");
  assert.strictEqual(await pane.locator(".nbrun-detail").count(), 1, "the open details stay open");
  // Opening Validate closes Runs: one panel beside the cells at a time.
  await listed.page.getByRole("button", { name: "Check" }).click();
  await listed.page.getByRole("menuitemcheckbox", { name: /^Validate/ }).click();
  await listed.page.locator(".validatepane").waitFor();
  assert.strictEqual(await listed.page.locator(".runspane").count(), 0);
});

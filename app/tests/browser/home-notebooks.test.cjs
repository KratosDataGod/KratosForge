// Home > Recent notebooks (home-notebooks.js): each notebook's details, Run several with the risk check
// for the Fabric ones, and items pinned to Home (see harness.cjs for how to run it).
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
after(async () => { await app?.close(); });

const notebooks = [{ name: "TPC-H data", modified: 1761480000 }, { name: "Testing Local Runs", modified: 1760780000 },
  { name: "Notebook 3", modified: 1760700000 }];
const details = {
  "TPC-H data": { name: "TPC-H data", workspace_id: "w1", workspace_name: "KF_Dev", lakehouse: "tpch_lakehouse", compute: "local",
    last_run: { source: "pc", status: "ok", started: "2026-10-29T08:00:00+00:00", ended: "2026-10-29T08:04:12+00:00", seconds: 252, trigger: "home" } },
  "Testing Local Runs": { name: "Testing Local Runs", workspace_id: "w2", workspace_name: "Sales_Dev", lakehouse: "bronze", compute: "fabric",
    last_run: { source: "fabric", status: "Failed", started: "2026-10-18T08:00:00+00:00", message: "ModuleNotFoundError" } },
  "Notebook 3": { name: "Notebook 3", workspace_id: "", workspace_name: "", lakehouse: "", compute: "local", last_run: null },
};
const warning = { id: "r1", result: "warning", label: "Warning", summary: "1 thing to know", note: "Advice only.",
  findings: [{ check: "failed_runs", outcome: "warning", title: "The last run failed: ModuleNotFoundError" }] };

async function home(extra = {}) {
  await app?.close();
  let pins = [];
  let batch = null;
  app = await openApp({ mocks: {
    environment: () => ({ python: "Python 3 (test)", user: "chris@example.com" }),
    list_notebooks: () => notebooks,
    notebook_details: ({ names }) => names.map((n) => details[n]),
    item_pins: () => pins,
    set_item_pin: ({ kind, id, name, on }) => { pins = on ? [...pins, { kind, id, name, workspace_id: "", workspace_name: "" }] : pins.filter((p) => p.id !== id); return pins; },
    risk_check: () => warning,
    risk_acknowledge: () => ({ ok: true }),
    notebook_batch_start: ({ names, parallel }) => (batch = { id: "abcdef123456", state: "starting", parallel,
      items: names.map((name) => ({ name, status: "waiting" })) }),
    notebook_batch: () => batch,
    notebook_batch_fabric: ({ name }) => ({ job_id: "j1", name, workspace_name: "Sales_Dev", url: "" }),
    ...extra,
  } });
  await app.page.waitForFunction(() => state.view === "home" && homeCache.done, null, { timeout: 20000 });
  return app.page;
}

test("each recent notebook shows its workspace, lakehouse, where it runs and its last run", { skip }, async () => {
  const p = await home();
  const rows = await p.$$eval(".home-notebooks tbody tr", (trs) => trs.map((tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent.trim())));
  assert.deepStrictEqual(rows[0].slice(1, 5), ["TPC-H data", "KF_Dev", "tpch_lakehouse", "This PC"]);
  assert.match(rows[0][5], /^Succeeded .*, 4m 12s$/);
  assert.deepStrictEqual(rows[1].slice(2, 5), ["Sales_Dev", "bronze", "Fabric"]);
  assert.match(rows[1][5], /^Failed in Fabric/);
  assert.deepStrictEqual(rows[2].slice(2, 6), ["Not published", "None", "This PC", "Never run"]);
  assert.deepStrictEqual(app.errors, []);
});

test("Run several asks first, shows the risk check for Fabric, and can run only the ones on this PC", { skip }, async () => {
  const p = await home();
  const run = p.locator(".home-run-bar button.primary");
  assert.ok(await run.isDisabled());
  await p.check('input[aria-label="Select TPC-H data"]');
  await p.check('input[aria-label="Select Testing Local Runs"]');
  assert.match(await run.textContent(), /^Run 2 notebooks/);
  await run.click();
  await p.waitForSelector(".home-run-confirm");
  const text = await p.textContent(".home-run-confirm");
  assert.match(text, /On this PC \(1\)/);
  assert.match(text, /On Fabric \(1\)/);
  assert.match(text, /The last run failed: ModuleNotFoundError/);
  assert.ok(app.calls.some((c) => c.method === "risk_check" && c.params.id === "notebook:Testing Local Runs" && c.params.action === "run_in_fabric"));
  await p.click('button:has-text("Run only the 1 on this PC")');
  await p.waitForFunction(() => homeRun.batch);
  const started = app.calls.find((c) => c.method === "notebook_batch_start");
  assert.deepStrictEqual(started.params.names, ["TPC-H data"]);
  assert.ok(!app.calls.some((c) => c.method === "notebook_batch_fabric"), "nothing started in Fabric");
  assert.match(await p.textContent(".home-run-bar"), /Running 1: 0 done, 1 waiting/);
  assert.deepStrictEqual(app.errors, []);
});

test("Run all from the confirm starts the Fabric one after acknowledging its warnings", { skip }, async () => {
  const p = await home();
  await p.check('input[aria-label="Select Testing Local Runs"]');
  await p.click(".home-run-bar button.primary");
  await p.waitForSelector(".home-run-confirm");
  await p.click('.dialog button.primary:has-text("Run Testing Local Runs")');
  await p.waitForFunction(() => activity.items.some((i) => i.id === "fabric:j1"));
  assert.ok(app.calls.some((c) => c.method === "risk_acknowledge" && c.params.risk_id === "r1"));
  assert.ok(app.calls.some((c) => c.method === "notebook_batch_fabric" && c.params.name === "Testing Local Runs"));
  assert.ok(!app.calls.some((c) => c.method === "notebook_batch_start"));
});

test("pinning a notebook puts it in Pinned at the top of Home", { skip }, async () => {
  const p = await home();
  assert.strictEqual(await p.locator(".home-pins").count(), 0);
  await p.click('button[aria-label="Pin TPC-H data to Home"]');
  assert.ok(app.calls.some((c) => c.method === "set_item_pin" && c.params.kind === "notebook" && c.params.id === "TPC-H data" && c.params.on === true));
  await p.click('button:text-is("Refresh")');
  await p.waitForSelector(".home-pins");
  assert.match(await p.textContent(".home-pins"), /TPC-H data/);
  assert.match(await p.textContent(".home-pins"), /KF_Dev · This PC/);
  assert.deepStrictEqual(app.errors, []);
});

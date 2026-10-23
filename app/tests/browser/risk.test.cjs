// FINOPS-004: the risk check in the workload run dialog, the notebook and Data mesh run gates, and
// the "Before a run" settings. The findings are fixtures shaped like risk.analyze's result.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const f = (check, outcome, title, detail, extra = {}) => ({ check, outcome, label: outcome[0].toUpperCase() + outcome.slice(1), title, detail,
  evidence: [], remedy: "", source: "", observed_at: null, essential: false, measurement: null, can_override: outcome === "warning", ...extra });
const NOTE = "Read only: nothing was started, changed or paused to work this out.";
const risk = (result, summary, findings, extra = {}) => ({ id: `r-${result}`, result, label: result[0].toUpperCase() + result.slice(1), summary,
  findings, can_proceed: result !== "blocked", needs_acknowledgement: false, note: NOTE, reads: [], ...extra });

const WARNING = risk("warning", "2 thing(s) to check before you run", [
  f("history", "warning", "The last run failed", "A run now may fail the same way until the cause is fixed.",
    { evidence: ["Today 11:30: Activity Copy failed"], remedy: "Open the run and fix the cause first." }),
  f("capacity", "warning", "fabcapdev was paused when last read", "It was not running 3 hours ago. If it is still paused, the run fails with CapacityNotActive.",
    { evidence: ["Capacity reading 3 hours ago", "F8"] }),
  f("policy", "pass", "Fabric runs are allowed by your organization", ""),
  f("consumption", "pass", "The last run used about 1.30 CU hours", "",
    { measurement: { value: 1.3, unit: "cu_hours", classification: "estimated" } }),
], { needs_acknowledgement: true });
const BLOCKED = risk("blocked", "This run can't start: fabcapdev is paused", [
  f("capacity", "blocked", "fabcapdev is paused", "Not running: paused, suspended or an expired trial. Fabric refuses new jobs until an admin resumes it.",
    { evidence: ["Capacity reading 4 minutes ago", "F8", "West Europe"], remedy: "Ask your capacity admin to resume it, or run it on this PC." }),
  f("local_alternative", "pass", "This PC could run it too", "Nothing in the notebook needs Fabric. Results may still differ: compare with Prove."),
]);
const PASS = risk("pass", "Nothing found that should stop this run", [
  f("capacity", "pass", "fabcaptest is running (read 12 minutes ago)", "How busy it is isn't known."),
  f("consumption", "pass", "The last run used about 0.42 CU hours", "",
    { measurement: { value: 0.42, unit: "cu_hours", classification: "measured" } }),
]);

const PRE = { allowed: true, label: "Run now", workload: "pl_load_erp", env_label: "", where: "Fabric · Sales Dev",
  identity: "You", consequence: "Starts pipeline pl_load_erp in Fabric now, exactly as Fabric's own Run does.",
  warnings: [], recorded: "Recorded on this workload as submitted, then as succeeded or failed when the run finishes." };

let app;
const FAILS = "fails";
let current = WARNING;
let checks = 0;
const acks = [];
const acted = [];
const saved = [];

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1000, height: 900 },
    mocks: {
      workload_preflight: () => ({ ...PRE, risk: current }),
      risk_check: (p) => {
        checks += 1;
        if (current === FAILS) throw new Error("This notebook isn't deployed to a Fabric workspace yet.");
        return p.id === "meta:sales:orders" ? BLOCKED : p.id === "meta:sales:customers" ? WARNING : current; },
      // Data mesh Run all reads the risk of every entity in one batch (#297).
      meta_risk_gate: (p) => {
        const all = { orders: BLOCKED, customers: WARNING };
        const checks = (p.names || Object.keys(all)).map((n) => ({ entity: n, ...all[n] }));
        const blocked = checks.filter((c) => c.result === "blocked").map((c) => c.entity);
        return { checks, blocked, held: {}, run: checks.map((c) => c.entity).filter((n) => !blocked.includes(n)), enforced: false };
      },
      risk_acknowledge: (p) => { acks.push(p); return { at: "now", risk_id: p.risk_id }; },
      workload_act: (p) => { acted.push(p); return { action: { status: "submitted" } }; },
      risk_settings: () => ({ warn_run_cu_hours: null, warn_failed_runs: 2, schedule_window_minutes: 60, require_acknowledgement: false }),
      set_cost_settings: () => ({}),
      set_risk_settings: (p) => { saved.push(p); return p; },
    },
  });
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const page = () => app.page;
const dialog = () => page().locator(".dialog");
const shot = (name) => SHOTS && dialog().screenshot({ path: path.join(SHOTS, name) });
const runDialog = () => page().evaluate(() => confirmWorkloadAction({ id: "fabric:ws:pl" }, { action: "run_item", label: "Run now" }));

test("warnings are shown with their evidence and the run waits for the tick when settings ask for it", { skip }, async () => {
  current = WARNING;
  await runDialog();
  await dialog().waitFor();
  const text = await dialog().textContent();
  assert.match(text, /2 thing\(s\) to check before you run/);
  assert.match(text, /The last run failed/);
  assert.match(text, /Today 11:30: Activity Copy failed/);
  assert.match(text, /2 other checks passed or aren't known/);
  assert.match(text, /Read only: nothing was started/);
  await page().locator(".risk-rest summary").click();
  await shot("risk-warning.png");
  await page().locator(".dialog-actions button", { hasText: "Run now" }).click();
  assert.strictEqual(acted.length, 0, "ran without the tick");
  assert.strictEqual(acks.length, 0);
  await page().locator(".risk-ack input").check();
  await page().locator(".dialog-actions button", { hasText: "Run now" }).click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.deepStrictEqual(acks.map((a) => a.risk_id), ["r-warning"]);
  assert.strictEqual(acted.length, 1);
  assert.deepStrictEqual(app.errors, []);
});

test("a blocked run has no run button, only Check again and Close", { skip }, async () => {
  current = BLOCKED;
  acted.length = 0;
  await runDialog();
  await dialog().waitFor();
  const buttons = await page().locator(".dialog-actions button").allTextContents();
  assert.deepStrictEqual(buttons, ["Check again", "Close"]);
  assert.match(await dialog().textContent(), /Ask your capacity admin to resume it/);
  const before = checks;
  await page().locator(".dialog-actions button", { hasText: "Check again" }).click();
  for (let i = 0; i < 100 && checks === before; i += 1) await new Promise((r) => setTimeout(r, 20));
  await dialog().waitFor();
  assert.ok(checks > before, "Check again didn't read the capacity again");
  await page().locator(".dialog-actions button", { hasText: "Close" }).click();
  assert.strictEqual(acted.length, 0);
});

test("a pass folds its checks away and the run goes ahead as before", { skip }, async () => {
  current = PASS;
  acks.length = 0;
  acted.length = 0;
  await runDialog();
  await dialog().waitFor();
  assert.strictEqual(await page().locator(".risk-panel.pass[open]").count(), 0);
  await page().locator(".risk-panel.pass summary").click();
  assert.match(await dialog().textContent(), /0.42 CU hours/);
  await shot("risk-pass.png");
  await page().locator(".dialog-actions button", { hasText: "Run now" }).click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.strictEqual(acks.length, 0, "a pass needs no acknowledgement");
  assert.strictEqual(acted.length, 1);
});

test("the notebook gate offers this PC when Fabric is blocked and stays quiet on a pass", { skip }, async () => {
  current = BLOCKED;
  const gate = page().evaluate(() => riskGate({ id: "notebook:nb", action: "run_in_fabric", title: "Run in Fabric nb_sales_daily?",
    runLabel: "Run in Fabric", alt: { label: "Run on this PC", run: () => { window.__local = true; } } }));
  await dialog().waitFor();
  assert.deepStrictEqual(await page().locator(".dialog-actions button").allTextContents(), ["Check again", "Run on this PC", "Close"]);
  await shot("risk-blocked.png");
  await page().locator(".dialog-actions button", { hasText: "Run on this PC" }).click();
  assert.strictEqual(await gate, false);
  assert.strictEqual(await page().evaluate(() => window.__local), true);
  current = PASS;
  assert.strictEqual(await page().evaluate(() => riskGate({ id: "notebook:nb", action: "run_in_fabric", title: "x" })), true);
  assert.strictEqual(await dialog().count(), 0);
});

test("a risk check that couldn't run says why and asks, never going ahead on its own", { skip }, async () => {
  current = FAILS;
  const ask = () => page().evaluate(() => riskGate({ id: "notebook:nb", action: "run_in_fabric", title: "Run in Fabric nb_sales_daily?", runLabel: "Run in Fabric" }));
  let gate = ask();
  await dialog().waitFor();
  const text = await dialog().textContent();
  assert.match(text, /Unknown/);
  assert.match(text, /The risk check couldn't run/);
  assert.match(text, /This notebook isn't deployed to a Fabric workspace yet\./);
  assert.deepStrictEqual(await page().locator(".dialog-actions button").allTextContents(), ["Cancel", "Continue"]);
  await shot("risk-check-failed.png");
  await page().locator(".dialog-actions button", { hasText: "Cancel" }).click();
  assert.strictEqual(await gate, false);
  gate = ask();
  await dialog().waitFor();
  await page().locator(".dialog-actions button", { hasText: "Continue" }).click();
  assert.strictEqual(await gate, true);
  current = PASS;
});

test("the Prices dialog saves the Before a run settings", { skip }, async () => {
  await page().evaluate(() => costSettingsDialog({ price_per_cu_hour: 0.18, currency: "USD", python_cu: 1, spark_cu: 8 }));
  await dialog().waitFor();
  assert.match(await dialog().textContent(), /Before a run/);
  await shot("risk-settings.png");
  await page().getByLabel("Warn above this many CU hours per run").fill("2.5");
  await page().locator(".dialog .risk-ack input").check();
  await page().locator(".dialog-actions button", { hasText: "Save" }).click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.deepStrictEqual(saved, [{ warn_run_cu_hours: 2.5, warn_failed_runs: 2, schedule_window_minutes: 60, require_acknowledgement: true }]);
});

test("Data mesh Run all starts only the entities that aren't blocked, and nothing when all are", { skip }, async () => {
  acks.length = 0;
  const p = { project: { name: "sales" }, entities: [{ name: "orders" }, { name: "customers" }] };
  const gate = page().evaluate((p) => metaRiskGate(p, ["orders", "customers"], "dev", "all 2 entities"), p);
  await dialog().waitFor();
  assert.match(await dialog().textContent(), /1 of 2 won't start; the other 1 run if you go ahead/);
  await shot("risk-data-mesh.png");
  await page().locator(".dialog .risk-ack input").check();
  await page().locator(".dialog-actions button", { hasText: "Run the other 1" }).click();
  assert.deepStrictEqual(await gate, ["customers"]);
  assert.deepStrictEqual(acks.map((a) => a.id), ["meta:sales:customers"]);
  const one = page().evaluate((p) => metaRiskGate(p, ["orders"], "dev", "orders"), p);
  await dialog().waitFor();
  assert.deepStrictEqual(await page().locator(".dialog-actions button").allTextContents(), ["Close"]);
  await page().locator(".dialog-actions button", { hasText: "Close" }).click();
  assert.strictEqual(await one, false);
});

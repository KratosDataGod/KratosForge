// Data mesh Run all as one batch in Forge animations (docs/forge-animations.md): one Pulse when the
// run starts, one Anvil Strike on the results when every table succeeded, one ember when any failed,
// nothing when a table waits on a decision, and never one effect per table. meta_run answers only
// when the test says, so the order of what plays is never a race.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const entity = (name, layer, source, reads = []) => ({ name, layer, source, reads, owner: "", checks: 1, clean: 0, derive: 0, custom: [], mode: "full", watermark: "", keys: [], columns: [] });
let answer = null;  // resolves the pending meta_run with the results the test gives
const mocks = {
  meta_project: () => ({
    path: "C:/work/orders", name: "Orders", templates: {},
    layers: [{ name: "bronze", lakehouse: "LH_Bronze", entities: 1 }, { name: "silver", lakehouse: "LH_Silver", entities: 2 }],
    entities: [entity("erp_orders", "bronze", "landing erp/orders"), entity("orders", "silver", "erp_orders", ["erp_orders"]), entity("customers", "silver", "landing erp/customers")],
    products: [], links: [], environments: {}, exceptions: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [],
    project: { name: "Orders", domain: "sales", subdomain: "", team: "" },
  }),
  mesh_map: () => ({ me: "Orders", problem: "", domains: [], projects: [], products: [], links: [] }),
  meta_runs: () => [],
  meta_risk_gate: () => ({ checks: [], blocked: [], held: {}, run: [], enforced: false }),
  meta_run: () => new Promise((resolve) => { answer = resolve; }),
};
const ok = (entity) => ({ entity, status: "ok", rows_written: 10, quarantined: 0 });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await page().emulateMedia({ reducedMotion: "no-preference" });
  await page().evaluate(() => { state.meta.tab = "entities"; go("metadata"); });
  await page().waitForSelector(".page-head .head-actions button:has-text('Run all')");
  // Effects drop a level while the machine is busy; the load watch is stopped so only the run decides.
  await page().evaluate(() => {
    motionLoadWatch?.disconnect(); loadTasks = []; dropUntil = 0; applyMotion();
    window.__fx = [];
    const once = new WeakSet();
    const seen = (e) => !once.has(e) && once.add(e) && window.__fx.push(`${e.className} @${e.parentElement?.className ?? ""}`);
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx")) seen(n);
      n.querySelectorAll?.(".fx").forEach(seen);
    }))).observe(document.body, { childList: true, subtree: true });
  });
});
after(async () => { if (app) await app.close(); });

// Runs the batch: Run all, the Pulse, then the results the test gives, then what played after them.
async function runAll(results) {
  answer = null;
  await page().evaluate(() => { window.__fx = []; closeDialog(); });
  await page().click(".page-head .head-actions button:has-text('Run all')");
  await page().waitForFunction(() => window.__fx.some((c) => c.includes("fx-pulse")));
  const before = await page().evaluate(() => window.__fx);
  assert.deepStrictEqual(before.map((c) => c.split(" @")[0]), ["fx fx-pulse"], "something besides the Pulse played before the results");
  assert.match(before[0], /@tool primary/, "the Pulse isn't on the Run all button");
  while (!answer) await page().waitForTimeout(20);
  answer(results);
  await page().waitForSelector("#dialog-root .dialog-head");
  // The results dialog and its effect come in the same task; one frame later all of it is recorded.
  await page().evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  return (await page().evaluate(() => window.__fx)).slice(1);
}

test("Run all pulses once, and strikes once on the results when every table succeeded", { skip }, async () => {
  const after = await runAll([ok("erp_orders"), ok("orders"), ok("customers")]);
  assert.match(await page().textContent("#dialog-root .dialog-head"), /3 of 3 succeeded/);
  assert.deepStrictEqual(after.map((c) => c.split(" @")[0]), ["fx fx-strike"], `not one strike for the batch: ${after}`);
  assert.match(after[0], /dialog-head/);
  assert.deepStrictEqual(app.errors, []);
});

test("A table that fails gives the batch one ember and no strike", { skip }, async () => {
  await page().waitForTimeout(1600);  // past the strike's own gap, so its absence below is the rule's
  const after = await runAll([ok("erp_orders"), { entity: "orders", status: "failed", message: "Source not found" }, ok("customers")]);
  assert.deepStrictEqual(after.map((c) => c.split(" @")[0]), ["fx fx-fail"], `not one ember for the batch: ${after}`);
  assert.deepStrictEqual(app.errors, []);
});

test("A table waiting on a decision plays nothing after the Pulse", { skip }, async () => {
  await page().waitForTimeout(1600);
  const after = await runAll([ok("erp_orders"), { entity: "orders", status: "needs_decision", message: "The logic changed", options: ["none", "full"] }]);
  assert.match(await page().textContent("#dialog-root .dialog-head"), /needs a decision/);
  assert.deepStrictEqual(after, [], `a batch waiting on a decision played: ${after}`);
  assert.deepStrictEqual(app.errors, []);
});

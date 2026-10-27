// Energy Flow on a pipeline run (KF-MO-004, docs/forge-animations.md): the run view draws its top-level
// activities as linked nodes with their state in words, counts ForEach children on the parent, says the
// run is on this PC, settles or fails a node once when it changes, pulses when a run starts from this
// app and strikes when it ends well. The run's status is mocked and moves only when the test says, so
// what the poll sees is never a race.
const { test, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const T0 = "2026-10-23T10:00:00Z";

const act = (id, name, type, state, extra = {}) => ({ id, name, type, state, started: state === "pending" ? null : T0,
  seconds: state === "pending" || state === "running" ? null : 1, executed_by: "engine", ...extra });
const kid = (i, state) => act(`k${i}`, "Mark region", "SetVariable", state, { parent: "b", label: `iteration ${i}` });
const run = (state, acts, extra = {}) => ({ run_id: "run-flow-1", pipeline: "Daily orders", state, started: T0,
  ended: ["running", "pending"].includes(state) ? null : T0, seconds: 3, where: "this_pc", verified_against_fabric: false,
  options: { mocked: [] }, activities: acts, notes: [], problems: [], ...extra });

// The steps a test walks its run through; the mocked status answers with the current one.
function steps(list) {
  const s = { list, at: 0 };
  s.next = () => { s.at = Math.min(s.at + 1, list.length - 1); };
  s.status = () => list[s.at];
  return s;
}

const apps = [];
async function open(walk) {
  const app = await openApp({ viewport: { width: 1440, height: 900 }, mocks: { pipeline_run_status: () => walk.status(), pipeline_runs: () => [] } });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion: "no-preference" });
  await app.page.waitForFunction(() => typeof openPipelineRun === "function" && !!document.querySelector(".rail-item"));
  // Effects drop a level while the machine is busy; the load watch is stopped so only the run decides.
  await app.page.evaluate(() => {
    motionLoadWatch?.disconnect(); loadTasks = []; dropUntil = 0; applyMotion();
    window.__fx = [];
    // Each effect once: the flow is moved into each redraw of the view, carrying a playing effect with it.
    const once = new WeakSet();
    const seen = (e) => !once.has(e) && once.add(e) && window.__fx.push(`${e.className} @${e.closest("[data-flow]")?.dataset.flow ?? e.parentElement?.className ?? ""}`);
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx")) seen(n);
      n.querySelectorAll?.(".fx").forEach(seen);
    }))).observe(document.body, { childList: true, subtree: true });
  });
  return app;
}

const states = (page) => page.$$eval(".pl-flow .flow-node", (els) => els.map((e) => `${e.querySelector("b").textContent}: ${e.querySelector(".flow-state").textContent}`));
const waitFor = (page, fn, arg) => page.waitForFunction(fn, arg, { timeout: 15000 });

after(async () => { for (const app of apps) await app.close(); });

test("A run started here pulses, its activities flow and settle once, and it strikes when it succeeds", { skip }, async () => {
  const walk = steps([
    run("running", [act("a", "Ingest orders", "Copy", "running"), act("b", "Per region", "ForEach", "pending")]),
    run("running", [act("a", "Ingest orders", "Copy", "succeeded"), act("b", "Per region", "ForEach", "running"), kid(0, "succeeded"), kid(1, "running"), kid(2, "pending")]),
    run("succeeded", [act("a", "Ingest orders", "Copy", "succeeded"), act("b", "Per region", "ForEach", "succeeded"), kid(0, "succeeded"), kid(1, "succeeded"), kid(2, "succeeded")]),
  ]);
  const { page, errors } = await open(walk);
  await page.evaluate(() => { plPulse = "run-flow-1"; openPipelineRun("run-flow-1"); });
  await page.waitForSelector(".pl-flow .flow-node");
  assert.strictEqual(await page.textContent(".pl-flow .flow-where"), "Local run on this PC (not run in Fabric)");
  assert.deepStrictEqual(await states(page), ["Ingest orders: In progress", "Per region: Queued"]);
  await waitFor(page, () => window.__fx.some((c) => c.includes("fx-pulse")));
  assert.strictEqual(await page.evaluate(() => plPulse), null, "the pulse was kept after the run view drew");
  walk.next();
  await waitFor(page, () => document.querySelector('.pl-flow [data-flow="b"]')?.dataset.state === "running");
  assert.deepStrictEqual(await states(page), ["Ingest orders: Succeeded", "Per region: In progress · 1 of 3"], "ForEach children aren't counted on their parent");
  assert.strictEqual(await page.locator(".pl-flow .flow-node").count(), 2, "a ForEach child was drawn as its own node");
  await waitFor(page, () => window.__fx.some((c) => c.includes("fx-settle") && c.endsWith("@a")));
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "pipeline-flow.png") });
  walk.next();
  await waitFor(page, () => window.__fx.some((c) => c.includes("fx-strike")));
  assert.deepStrictEqual(await states(page), ["Ingest orders: Succeeded", "Per region: Succeeded · 3 of 3"]);
  const made = await page.evaluate(() => window.__fx);
  assert.strictEqual(made.filter((c) => c.includes("fx-settle") && c.endsWith("@a")).length, 1, `a node settled more than once: ${made}`);
  assert.strictEqual(made.filter((c) => c.includes("fx-pulse")).length, 1, `the run pulsed more than once: ${made}`);
  assert.ok(!made.some((c) => c.includes("fx-fail")), `a successful run made an ember: ${made}`);
  assert.deepStrictEqual(errors, []);
});

test("A failed activity shows one ember and the run asks for its own; a run opened after it ended plays nothing", { skip }, async () => {
  const walk = steps([
    run("running", [act("a", "Ingest orders", "Copy", "running"), act("b", "Per region", "ForEach", "pending")]),
    run("failed", [act("a", "Ingest orders", "Copy", "failed", { error: { message: "Source not found" } }), act("b", "Per region", "ForEach", "cancelled", { executed_by: "not_started" })]),
  ]);
  const { page, errors } = await open(walk);
  await page.evaluate(() => openPipelineRun("run-flow-1"));
  await page.waitForSelector(".pl-flow .flow-node");
  walk.next();
  // The run's own ember asks once the follow ends; within the ember's gap after the node's, it is
  // skipped rather than stacked, so the failure shows one ember.
  await waitFor(page, () => motionDiagnostics().events["pipeline.failed"] === 1);
  const made = await page.evaluate(() => window.__fx);
  assert.strictEqual(made.filter((c) => c.includes("fx-fail") && c.endsWith("@a")).length, 1, `${made}`);
  assert.ok(!made.some((c) => /fx-(pulse|strike|settle)/.test(c)), `a run opened from Runs pulsed, or a failure struck: ${made}`);
  assert.deepStrictEqual(await states(page), ["Ingest orders: Failed", "Per region: Cancelled"]);
  // Opening the ended run again draws it as it ended, with no effect.
  await page.evaluate(() => { window.__fx = []; go("pipelines"); openPipelineRun("run-flow-1"); });
  await page.waitForSelector(".pl-flow .flow-node");
  await waitFor(page, () => document.querySelector(".pl-head h1")?.textContent.includes("Failed"));
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), []);
  assert.deepStrictEqual(errors, []);
});

test("A run with mocks doesn't strike, since it isn't a real success", { skip }, async () => {
  const walk = steps([
    run("running", [act("a", "Ingest orders", "Copy", "running", { executed_by: "mock" })], { options: { mocked: ["Ingest orders"] } }),
    run("succeeded", [act("a", "Ingest orders", "Copy", "succeeded", { executed_by: "mock" })], { options: { mocked: ["Ingest orders"] } }),
  ]);
  const { page, errors } = await open(walk);
  await page.evaluate(() => openPipelineRun("run-flow-1"));
  await page.waitForSelector(".pl-flow .flow-node");
  walk.next();
  await waitFor(page, () => window.__fx.some((c) => c.includes("fx-settle")));
  // The follow has ended once Runs' activity entry says so; only then would the strike have played.
  await waitFor(page, () => !plFollowing.size);
  assert.ok(!(await page.evaluate(() => window.__fx)).some((c) => c.includes("fx-strike")), "a mocked run struck");
  assert.deepStrictEqual(errors, []);
});

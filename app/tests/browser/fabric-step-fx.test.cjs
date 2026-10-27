// A Fabric step running in Fabric (docs/forge-animations.md): its output line shows the navy wolf
// trotting in place beside what Fabric says, with no fill or percentage; its running ember is navy,
// since the work isn't on this PC; and its label glows once when Fabric completes it, or shows the
// ember when it fails. Cancelled plays nothing. Fabric is mocked; the gates before the run are stood
// in for, and the run's state moves only when the test says.
const { test, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const apps = [];
async function open(fabric, { level = 2 } = {}) {
  const app = await openApp({ mocks: {
    app_settings: () => ({ motion_level: { value: level } }),
    notebook_fabric_step_check: () => ({ ok: true, item_name: "Gold load step", workspace_name: "Sales Dev" }),
    run_notebook_fabric_step: () => ({ status: "NotStarted", request_id: "fds-1", job_id: "job-1", workspace_id: "ws", item_id: "item-1",
      workspace_name: "Sales Dev", url: "https://app.fabric.microsoft.com/x" }),
    notebook_fabric_step_result: () => ({ status: fabric.status, url: "https://app.fabric.microsoft.com/x", tables: [] }),
    fabric_runs: () => [],
  } });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion: "no-preference" });
  await app.page.waitForFunction(() => typeof runFabricStep === "function" && !!document.querySelector(".rail-item"));
  await app.page.evaluate(async () => {
    await loadSettings();
    loadIntoState({ name: "Gold load", cells: [{ type: "code", cell_id: "s", source: "spark.sql('OPTIMIZE t')", meta: { fabric_step: true } }] });
    go("notebook");
    // The gates before a run on Fabric, stood in for: signed in, nothing to stop, confirmed.
    ensureSignedIn = async () => true;
    riskGate = async () => true;
    stepConfirm = async () => true;
    trackFabricJobs = () => {};
    motionLoadWatch?.disconnect(); loadTasks = []; dropUntil = 0; applyMotion();
    window.__fx = [];
    const once = new WeakSet();
    const seen = (e) => !once.has(e) && once.add(e) && window.__fx.push(`${e.className} @${e.parentElement?.classList[0] ?? ""}`);
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx")) seen(n);
      n.querySelectorAll?.(".fx").forEach(seen);
    }))).observe(document.body, { childList: true, subtree: true });
  });
  return app;
}

const run = (page) => page.evaluate(() => { window.__done = runFabricStep(state.notebook.cells[0], state.notebook); });
const wait = (page, fn) => page.waitForFunction(fn, null, { timeout: 20000 });

after(async () => { for (const app of apps) await app.close(); });

test("A Fabric step shows the navy wolf and a navy ember while it runs, and glows once when it completes", { skip }, async () => {
  const fabric = { status: "InProgress" };
  const { page, errors } = await open(fabric);
  await run(page);
  await page.waitForSelector(".fabric-step-line .remote-progress .wolf-idle");
  assert.match(await page.textContent(".fabric-step-line .remote-where"), /^(Queued|Running) on Fabric/);
  assert.strictEqual(await page.getAttribute(".fabric-step-line [role=progressbar]", "aria-valuenow"), null);
  const ember = await page.evaluate(() => getComputedStyle(document.querySelector('.cell-status[data-where="fabric"] > span'), "::before").backgroundColor);
  const navy = await page.evaluate(() => { const e = document.createElement("i"); e.style.color = "var(--accent)"; document.body.append(e); const c = getComputedStyle(e).color; e.remove(); return c; });
  assert.strictEqual(ember, navy, "the running ember of a Fabric step isn't navy");
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "fabric-step-wolf.png") });
  fabric.status = "Completed";
  await wait(page, () => window.__fx.some((c) => c.includes("fx-arrive")));
  const made = await page.evaluate(() => window.__fx);
  assert.deepStrictEqual(made.map((c) => c.split(" @")[1]), ["ok"], `${made}`);
  assert.strictEqual(await page.locator(".fabric-step-line .wolf-idle").count(), 0, "the wolf stayed after the step ended");
  assert.deepStrictEqual(errors, []);
});

test("A Fabric step that fails shows the ember once; a cancelled one plays nothing", { skip }, async () => {
  for (const [end, want] of [["Failed", ["fx fx-fail @bad"]], ["Cancelled", []]]) {
    const fabric = { status: "InProgress" };
    const { page, errors } = await open(fabric);
    await run(page);
    await page.waitForSelector(".fabric-step-line .remote-progress");
    fabric.status = end;
    await wait(page, () => window.__done && state.notebook.cells[0].status !== "running");
    await page.waitForTimeout(200);
    assert.deepStrictEqual(await page.evaluate(() => window.__fx), want, end);
    assert.deepStrictEqual(errors, []);
  }
});

test("With motion Off the step still says it is running on Fabric, with no wolf or glow", { skip }, async () => {
  const fabric = { status: "InProgress" };
  const { page, errors } = await open(fabric, { level: 0 });
  await run(page);
  await page.waitForSelector(".fabric-step-line .remote-where");
  assert.strictEqual(await page.locator(".wolf-idle").count(), 0);
  fabric.status = "Completed";
  await wait(page, () => state.notebook.cells[0].status === "ok");
  await page.waitForTimeout(200);
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), []);
  assert.deepStrictEqual(errors, []);
});

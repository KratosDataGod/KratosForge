// A notebook run in Fabric (docs/forge-animations.md): Run all pulses when Fabric has the run, the
// status bar shows the navy wolf trotting in place with no fill or percentage while it goes, then
// glows once when it completes or shows the ember when it fails. Cancelled plays nothing. Fabric is
// mocked; the gates before a run (sign-in, risk check, Fabric changes) are stood in for, and the run's
// state moves only when the test says, so nothing here races the poll.
const { test, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const apps = [];
async function open(fabric, { level = 2, reducedMotion = "no-preference" } = {}) {
  const app = await openApp({ mocks: {
    app_settings: () => ({ motion_level: { value: level } }),
    run_notebook_in_fabric: () => ({ job_id: "job-1", status: "NotStarted", workspace_name: "Sales", url: "https://app.fabric.microsoft.com/x" }),
    fabric_runs: () => [{ job_id: "job-1", status: fabric.status }],
  } });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion });
  await app.page.waitForFunction(() => typeof runInFabric === "function" && !!document.querySelector(".rail-item"));
  await app.page.evaluate(async () => {
    await loadSettings();
    loadIntoState({ name: "Daily sales", cells: [{ type: "code", cell_id: "a", source: "x = 1", meta: {} }] });
    go("notebook");
    // The gates before a run in Fabric, stood in for: signed in, attached, nothing to confirm.
    nbLakehouse = () => ({ name: "Sales_LH" });
    ensureSignedIn = async () => true;
    riskGate = async () => true;
    nbFabricChangeGate = async () => ({ replace: false });
    trackFabricJobs = () => {};
    fabricWatchMs = 50;
    motionLoadWatch?.disconnect(); loadTasks = []; dropUntil = 0; applyMotion();
    window.__fx = [];
    const once = new WeakSet();
    const seen = (e) => !once.has(e) && once.add(e) && window.__fx.push(`${e.className} @${e.parentElement?.className ?? ""}`);
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx")) seen(n);
      n.querySelectorAll?.(".fx").forEach(seen);
    }))).observe(document.body, { childList: true, subtree: true });
  });
  return app;
}

const fx = (page) => page.evaluate(() => window.__fx);
const wait = (page, fn) => page.waitForFunction(fn, null, { timeout: 15000 });

after(async () => { for (const app of apps) await app.close(); });

test("Run in Fabric pulses, shows the navy wolf while it runs, and glows once when it completes", { skip }, async () => {
  const fabric = { status: "InProgress" };
  const { page, errors } = await open(fabric);
  await page.evaluate(() => runInFabric());
  await wait(page, () => window.__fx.some((c) => c.includes("fx-pulse")));
  await wait(page, () => /Running in Fabric: InProgress/.test(document.querySelector(".statusbar .nb-fabric-run")?.textContent || ""));
  const bar = page.locator(".statusbar .nb-fabric-run [role=progressbar]");
  assert.strictEqual(await bar.getAttribute("aria-valuenow"), null, "a run in Fabric showed a measured value");
  assert.ok(await page.locator(".statusbar .nb-fabric-run .wolf-idle").count(), "no wolf while the run goes");
  assert.doesNotMatch(await page.textContent(".statusbar"), /%/);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "fabric-run-wolf.png") });
  fabric.status = "Completed";
  await wait(page, () => window.__fx.some((c) => c.includes("fx-arrive") && c.includes("nb-fabric-last")));
  assert.strictEqual(await page.textContent(".statusbar .nb-fabric-last"), "Last Fabric run: Completed");
  assert.strictEqual(await page.locator(".statusbar .wolf-idle").count(), 0, "the wolf stayed after the run ended");
  const made = await fx(page);
  assert.strictEqual(made.filter((c) => c.includes("fx-arrive")).length, 1, `${made}`);
  assert.ok(!made.some((c) => c.includes("fx-fail")), `${made}`);
  assert.deepStrictEqual(errors, []);
});

test("A run in Fabric that fails shows the ember once; a cancelled one plays nothing", { skip }, async () => {
  for (const [end, kind] of [["Failed", "fx-fail"], ["Cancelled", null]]) {
    const fabric = { status: "InProgress" };
    const { page, errors } = await open(fabric);
    await page.evaluate(() => runInFabric());
    await wait(page, () => !!document.querySelector(".statusbar .nb-fabric-run"));
    await page.evaluate(() => { window.__fx = []; });
    fabric.status = end;
    // The status bar is redrawn and the effect, if any, played in the same step of the watch.
    await wait(page, () => !!document.querySelector(".statusbar .nb-fabric-last"));
    const made = await fx(page);
    if (kind) assert.ok(made.some((c) => c.includes(kind) && c.includes("nb-fabric-last")), `${end}: ${made}`);
    assert.strictEqual(made.length, kind ? 1 : 0, `${end}: ${made}`);
    assert.deepStrictEqual(errors, []);
  }
});

test("With motion Off or reduced motion the run still says it is running in Fabric, with no wolf", { skip }, async () => {
  for (const opts of [{ level: 0 }, { reducedMotion: "reduce" }]) {
    const fabric = { status: "InProgress" };
    const { page, errors } = await open(fabric, opts);
    await page.evaluate(() => runInFabric());
    await wait(page, () => /Running in Fabric: InProgress/.test(document.querySelector(".statusbar .nb-fabric-run")?.textContent || ""));
    assert.strictEqual(await page.locator(".statusbar .wolf-idle").count(), 0, `a wolf ran with ${JSON.stringify(opts)}`);
    fabric.status = "Completed";
    await wait(page, () => !!document.querySelector(".statusbar .nb-fabric-last"));
    assert.deepStrictEqual(await fx(page), [], `an effect played with ${JSON.stringify(opts)}`);
    assert.deepStrictEqual(errors, []);
  }
});

// The cooling spark (docs/forge-animations.md, KF-MO-006): a passed Validate clean run, a proof
// whose runs match and a publish Fabric confirmed glow forge orange and cool; a failed one gets the
// ember instead. The clean run, the proof and Fabric are mocked, so nothing reaches a kernel or a tenant.
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const CELLS = [{ type: "code", cell_id: "a", source: "x = 1", meta: {} }];

const apps = [];
async function open(mocks, engine = []) {
  const app = await openApp({ viewport: { width: 1440, height: 900 }, engine, mocks: { save_notebook: () => ({ saved: true }), ...mocks } });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion: "no-preference" });
  await app.page.evaluate((cells) => {
    loadIntoState({ name: "Daily sales", cells });
    state.ribbonTab = "run";
    go("notebook");
    // Every effect made, with what it was laid over; the load watch is stopped so the runner's load
    // can't drop the level (as in forge-animations.test.cjs).
    motionLoadWatch?.disconnect();
    loadTasks = [];
    dropUntil = 0;
    applyMotion();
    window.__fx = [];
    const seen = (e) => window.__fx.push(`${e.className} @${e.parentElement?.id || e.parentElement?.className || ""}`);
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx")) seen(n);
    }))).observe(document.body, { childList: true, subtree: true });
  }, CELLS);
  return app;
}

async function madeFx(page, kind, where) {
  try {
    await page.waitForFunction(([kind, where]) => window.__fx.some((c) => c.startsWith(`fx fx-${kind} `) && c.includes(where)),
      [kind, where], { timeout: 30000 });
  } catch {
    assert.fail(`no ${kind} on ${where}: ${JSON.stringify(await page.evaluate(() => [window.__fx, motionDiagnostics()]))}`);
  }
}

after(async () => { for (const app of apps) await app.close(); });

// The code checks come from the real engine; the clean run is mocked: running at first, then
// finished with this verdict.
const VALIDATE_ENGINE = ["notebook_validate_check", "notebook_validate_list", "notebook_cleanup_plan"];
function validateMocks(verdict) {
  let polls = 0;
  const counts = verdict === "ready" ? {} : { error: 1 };
  const findings = verdict === "ready" ? [] : [{ severity: "error", title: "Cell 1 failed", cell: 1, found_by: ["run"] }];
  return {
    notebook_validate_start: () => ({ id: "v1" }),
    notebook_validate_status: () => (polls++ < 1 ? { id: "v1", state: "running", cells: [] }
      : { id: "v1", state: "done", verdict, counts, findings, cells: [{ cell: 1, status: verdict === "ready" ? "ok" : "error" }],
        summary: verdict === "ready" ? "Ready: every cell ran from the top." : "Not ready: cell 1 failed." }),
  };
}

test("A clean run that passes cools the verdict banner, with no ember", { skip }, async () => {
  const { page, errors } = await open(validateMocks("ready"), VALIDATE_ENGINE);
  await page.getByRole("button", { name: /^Validate/ }).click();
  const pane = page.locator(".validatepane");
  await pane.getByRole("button", { name: "Run clean check" }).click();
  await madeFx(page, "cool", "prove-banner ok");
  await pane.getByText("Ready: every cell ran from the top.").waitFor();
  assert.ok(!(await page.evaluate(() => window.__fx)).some((c) => c.includes("fx-fail")), "a passed run showed the ember");
  assert.deepStrictEqual(errors, []);
});

test("A clean run that fails gets the ember beside its verdict, not the cooling spark", { skip }, async () => {
  const { page, errors } = await open(validateMocks("not_ready"), VALIDATE_ENGINE);
  await page.getByRole("button", { name: /^Validate/ }).click();
  await page.locator(".validatepane").getByRole("button", { name: "Run clean check" }).click();
  await madeFx(page, "fail", "@");
  const made = await page.evaluate(() => window.__fx);
  assert.ok(made.some((c) => c.startsWith("fx fx-fail ")), JSON.stringify(made));
  assert.ok(!made.some((c) => c.includes("fx-cool")), "a failed run cooled");
  assert.deepStrictEqual(errors, []);
});

test("A publish Fabric confirmed cools the toast; a failed publish gets the ember", { skip }, async () => {
  let fail = false;
  const { page, errors } = await open({
    notebook_publish_check: () => ({ changed: false, known: true }),
    publish_notebook: (p) => {
      if (fail) throw new Error("Fabric said no");
      return { name: p.name, workspace_id: "ws1", workspace_name: "Sales WS", id: "item-1" };
    },
  });
  await page.evaluate(() => { state.user = "chris@example.com"; state.notebook.workspace = { workspace_id: "ws1", workspace_name: "Sales WS" }; publishNotebook(); });
  await madeFx(page, "cool", "@toast");
  await page.waitForFunction(() => /^Published Daily sales/.test(document.getElementById("toast").textContent));
  fail = true;
  await page.evaluate(() => publishNotebook());
  await madeFx(page, "fail", "@toast");
  assert.match(await page.evaluate(() => document.getElementById("toast").textContent), /Publish failed/);
  assert.deepStrictEqual(errors, []);
});

// forgeEvent counts every event it is told about, even with no anchor to play on, so an empty count
// means the code never asked for an effect.
const eventsNamed = (page, prefix) => page.evaluate((p) => Object.keys(motionDiagnostics().events).filter((n) => n.startsWith(p)), prefix);

test("A cancelled clean run plays nothing", { skip }, async () => {
  let polls = 0;
  const { page, errors } = await open({
    notebook_validate_start: () => ({ id: "v1" }),
    notebook_validate_status: () => (polls++ < 1 ? { id: "v1", state: "running", cells: [] } : { id: "v1", state: "cancelled", cells: [] }),
  }, VALIDATE_ENGINE);
  await page.getByRole("button", { name: /^Validate/ }).click();
  await page.locator(".validatepane").getByRole("button", { name: "Run clean check" }).click();
  await page.waitForFunction(() => document.getElementById("toast").textContent === "Daily sales: cancelled");
  await page.waitForFunction(() => state.notebook.validate.following === null);
  assert.deepStrictEqual(await eventsNamed(page, "validate."), []);
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), []);
  assert.deepStrictEqual(errors, []);
});

test("A publish whose sign-in was cancelled plays nothing", { skip }, async () => {
  const { page, errors } = await open({
    notebook_publish_check: () => ({ changed: false, known: true }),
    publish_notebook: () => { throw new Error("SignInCancelled: the sign-in window was closed"); },
  });
  await page.evaluate(async () => {
    state.user = "chris@example.com";
    state.notebook.workspace = { workspace_id: "ws1", workspace_name: "Sales WS" };
    await publishNotebook();
  });
  assert.deepStrictEqual(await eventsNamed(page, "publish."), []);
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), []);
  assert.deepStrictEqual(errors, []);
});

test("A proof whose runs match cools its verdict; one that differs gets the ember", { skip }, async () => {
  for (const [verdict, kind, other] of [["same", "cool", "fail"], ["differs", "fail", "cool"]]) {
    let polls = 0;
    const { page, errors } = await open({
      prove_list: () => [],
      prove_status: ({ id }) => (polls++ < 1 ? { id, name: "Daily sales", state: "verifying", pc: {}, fabric: {} }
        : { id, name: "Daily sales", state: "done", pc: {}, fabric: {}, tables: [], scorecard: {},
          verdict: { verdict, text: verdict === "same" ? "Both runs made the same results." : "The results differ in 1 table." } }),
    });
    await page.evaluate(() => {
      const nb = state.notebook;
      nb.prove = { record: { id: "p1", name: nb.name, state: "verifying", pc: {}, fabric: {} } };
      openProve(nb);
      followProof(nb, "p1");
    });
    await madeFx(page, kind, "pill-s");
    assert.ok(!(await page.evaluate(() => window.__fx)).some((c) => c.includes(`fx-${other}`)), `a ${verdict} proof played ${other}`);
    assert.deepStrictEqual(errors, []);
  }
});

// Git push, pull, fetch and clone (docs/forge-animations.md): while Git gives no measure the band
// shows the navy wolf trotting in place, keeping its stride as Git's words change; once Git counts
// objects the bar shows its percentage. A push, pull or clone that is done sparks on the message, a
// failure shows the ember, and a fetch or a cancel plays nothing. Git is mocked and moves only when
// the test says.
const { test, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const apps = [];
async function open(git, { level = 2 } = {}) {
  const app = await openApp({ mocks: {
    app_settings: () => ({ motion_level: { value: level } }),
    git_op_status: () => ({ id: "op-1", kind: git.kind, repo: "C:/src/sales", ...git.now }),
  } });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion: "no-preference" });
  await app.page.waitForFunction(() => typeof gitWatch === "function" && !!document.querySelector(".rail-item"));
  await app.page.evaluate(async () => {
    await loadSettings();
    // The Source control page stood in for: its band, and no refresh afterwards.
    gitRefresh = async () => {};
    gitLoadInfo = async () => {};
    document.querySelector("main, #main, body").prepend(gitOpBand());
    motionLoadWatch?.disconnect(); loadTasks = []; dropUntil = 0; applyMotion();
    window.__fx = [];
    const once = new WeakSet();
    const seen = (e) => !once.has(e) && once.add(e) && window.__fx.push(`${e.className} @${e.parentElement?.id || e.parentElement?.classList[0] || ""}`);
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx")) seen(n);
      n.querySelectorAll?.(".fx").forEach(seen);
    }))).observe(document.body, { childList: true, subtree: true });
  });
  return app;
}

const start = (page, kind) => page.evaluate((kind) => gitWatch({ id: "op-1", kind, repo: "C:/src/sales", status: "running", phase: "Starting", percent: null }), kind);
const wait = (page, fn, arg) => page.waitForFunction(fn, arg, { timeout: 15000 });

after(async () => { for (const app of apps) await app.close(); });

test("A push shows the navy wolf while Git talks to the remote, the percentage once it counts, and sparks when done", { skip }, async () => {
  const git = { kind: "push", now: { status: "running", phase: "Starting", percent: null } };
  const { page, errors } = await open(git);
  await start(page, "push");
  await page.waitForSelector("#git-op .wolf-idle");
  assert.strictEqual(await page.textContent("#git-op .remote-where"), "Talking to the remote");
  assert.strictEqual(await page.getAttribute("#git-op [role=progressbar]", "aria-valuenow"), null);
  assert.doesNotMatch(await page.textContent("#git-op"), /%/);
  // Git's words change: the same wolf keeps trotting (the band isn't drawn again).
  await page.evaluate(() => { window.__wolf = document.querySelector("#git-op .wolf-idle"); });
  git.now = { status: "running", phase: "Waiting for sign-in", percent: null };
  await wait(page, () => document.querySelector("#git-op .remote-where")?.textContent === "Waiting for sign-in");
  assert.ok(await page.evaluate(() => window.__wolf.isConnected), "the wolf was drawn again and lost its stride");
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "git-op-wolf.png") });
  git.now = { status: "running", phase: "Writing objects", percent: 40 };
  await wait(page, () => /Writing objects 40%/.test(document.querySelector("#git-op")?.textContent || ""));
  assert.strictEqual(await page.getAttribute("#git-op [role=progressbar]", "aria-valuenow"), "40");
  assert.strictEqual(await page.locator("#git-op .wolf-idle").count(), 0, "a measured push kept the trotting wolf");
  git.now = { status: "ok", message: "Pushed main to origin." };
  await wait(page, () => window.__fx.some((c) => c.includes("fx-spark")));
  const made = await page.evaluate(() => window.__fx);
  assert.deepStrictEqual(made.map((c) => c.split(" @")[1]), ["toast"], `${made}`);
  assert.deepStrictEqual(errors, []);
});

test("A failed pull shows the ember; a fetch that is done and a cancelled push play nothing", { skip }, async () => {
  for (const [kind, end, want] of [["pull", { status: "failed", message: "Couldn't reach origin" }, ["fx fx-fail @toast"]],
    ["fetch", { status: "ok", message: "Fetched origin." }, []], ["push", { status: "cancelled", message: "Cancelled" }, []]]) {
    const git = { kind, now: { status: "running", phase: "Starting", percent: null } };
    const { page, errors } = await open(git);
    await start(page, kind);
    await page.waitForSelector("#git-op [role=progressbar]");
    git.now = end;
    await wait(page, () => !gitPage.op);
    await page.waitForTimeout(200);
    assert.deepStrictEqual((await page.evaluate(() => window.__fx)).map((c) => c.replace(/ (error|show)\b/g, "")), want, `${kind} ${end.status}`);
    assert.deepStrictEqual(errors, []);
  }
});

test("With motion Off the band still says what Git is doing, with the plain bar and no wolf", { skip }, async () => {
  const git = { kind: "push", now: { status: "running", phase: "Starting", percent: null } };
  const { page, errors } = await open(git, { level: 0 });
  await start(page, "push");
  await page.waitForSelector("#git-op .remote-where");
  assert.strictEqual(await page.locator("#git-op .wolf-idle").count(), 0);
  assert.ok(await page.locator("#git-op .bar.indeterminate").isVisible());
  git.now = { status: "ok", message: "Pushed main to origin." };
  await wait(page, () => !gitPage.op);
  await page.waitForTimeout(200);
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), []);
  assert.deepStrictEqual(errors, []);
});

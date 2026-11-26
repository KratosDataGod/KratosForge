// activity.js beyond activity-coverage and the other Activity tray tests: friendlyError for each error about a
// file or folder on this PC (with and without a path), a time limit that is really the network, and an error with
// no reason; errorPanel's hint, Retry and Copy (copied, and refused); a run left running when the app closed reads
// as Unknown; storage that can't be written; a run waiting on a choice and one with quarantined rows; Cancel run
// for a run Fabric no longer lists and one that already ended; the tray's empty filters; and the environment picker.
// Nothing reaches Fabric: fabric_runs is mocked in the engine's shape.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const tray = () => page().locator("#tray-root .tray");
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);
let runs = [];

before(async () => {
  if (skip) return;
  app = await openApp({
    // A run that was still running when the app closed.
    storage: { "fd.activity": JSON.stringify({ items: [{ id: "left", status: "running", title: "Publish to Test", at: new Date().toISOString() }], seenAt: 0 }) },
    mocks: { fabric_runs: () => runs },
  });
});
after(async () => { if (app) await app.close(); });
beforeEach(async () => {
  if (skip) return;
  runs = [];
  await page().evaluate(() => { closeDialog(); toastClose(); });
  app.errors.length = 0;
});

test("friendlyError says each file error in words, with and without a path, and a network time limit as the network", { skip }, async () => {
  const out = await page().evaluate(() => [
    "FileNotFoundError: 'C:/data/a.csv'",
    "PermissionError: ''",
    "FileNotFoundError: [Errno 2] No such file or directory",
    "FileExistsError: [Errno 17] File exists",
    "IsADirectoryError: [Errno 21] Is a directory: 'C:\\\\data'",
    "IsADirectoryError: [Errno 21] Is a directory",
    "NotADirectoryError: [Errno 20] Not a directory",
    "PermissionError: [WinError 5] Access is denied",
    "TimeoutError: [Errno 110] Connection timed out",
    "",
  ].map((raw) => friendlyError(raw).text));
  assert.deepStrictEqual(out, [
    "C:/data/a.csv",
    "The app couldn't use a file on this PC.",
    "There is no file or folder at that path on this PC. Check the path, or pick the file again.",
    "Something already exists at that path on this PC. Pick another name or folder.",
    "C:\\data is a folder, and a file was expected. Pick a file.",
    "That path is a folder, and a file was expected. Pick a file.",
    "Part of that path is a file, and a folder was expected. Check the path.",
    "This PC didn't allow access to that file or folder. Close any program that has it open, or pick a folder you can write to.",
    "Couldn't reach Fabric. Check the internet connection and try again.",
    "Something went wrong and no reason was given. Try again; if it keeps happening, report a problem.",
  ]);
  assert.deepStrictEqual(app.errors, []);
});

test("an error panel says to report it, retries, and copies its details or says it couldn't", { skip }, async () => {
  await page().evaluate(() => {
    window.__retries = 0;
    let allow = true;
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { if (!allow) throw new Error("denied"); window.__copied = t; } } });
    window.__denyCopy = () => { allow = false; };
    const box = document.createElement("div");
    box.id = "ep-test";
    box.append(errorPanel("OSError: boom", {}), errorPanel("RuntimeError: try me", { retry: async () => { window.__retries++; } }));
    document.body.append(box);
  });
  const panels = page().locator("#ep-test .error-panel");
  assert.match(await panels.nth(0).innerText(), /If it keeps happening, copy the details into Report a problem\./);
  await panels.nth(1).getByRole("button", { name: "Retry" }).click();
  await page().waitForFunction(() => window.__retries === 1 && !document.querySelector("#ep-test .error-panel-retry").disabled);
  await panels.nth(0).locator("summary").click();
  await panels.nth(0).getByRole("button", { name: "Copy the details" }).click();
  await toastSays(/^Copied the details$/);
  assert.strictEqual(await page().evaluate(() => window.__copied), "OSError: boom");
  await page().evaluate(() => { toastClose(); window.__denyCopy(); });
  await panels.nth(0).getByRole("button", { name: "Copy the details" }).click();
  await toastSays(/^Couldn't copy\. Select the text and copy it instead\.$/);
  await page().evaluate(() => document.getElementById("ep-test").remove());
  assert.deepStrictEqual(app.errors, []);
});

test("a run left running when the app closed reads as Unknown; the Running and Problems filters can be empty", { skip }, async () => {
  assert.deepStrictEqual(await page().evaluate(() => activity.items.filter((i) => i.id === "left").map((i) => i.stale)), [true]);
  await page().evaluate(() => { activity.items = activity.items.filter((i) => i.id === "left"); activity.filter = "running"; toggleTray(true); });
  await tray().getByText("Nothing here.").waitFor();
  await tray().getByRole("button", { name: /^Problems 0$/ }).click();
  await tray().getByText("Nothing here.").waitFor();
  await tray().getByRole("button", { name: "All" }).click();
  assert.match(await tray().locator(".tray-item", { hasText: "Publish to Test" }).innerText(), /Unknown/);
  await tray().getByRole("button", { name: "Close" }).click();
  await tray().waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("storage that can't be written leaves the tray and the environment working for this session", { skip }, async () => {
  const out = await page().evaluate(() => {
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = () => { throw new Error("QuotaExceededError"); };
    try {
      logActivity({ id: "quota", status: "ok", title: "Saved while storage is full" });
      const view = state.view;
      state.view = "home";
      setGlobalEnv("test");
      state.view = view;
      return [activity.items[0].title, state.meta.env];
    } finally { Storage.prototype.setItem = set; setGlobalEnv("dev"); }
  });
  assert.deepStrictEqual(out, ["Saved while storage is full", "test"]);
  assert.deepStrictEqual(app.errors, []);
});

test("a run waiting on a choice says so, and one with quarantined rows counts them", { skip }, async () => {
  const out = await page().evaluate(() => [
    runReason({ status: "needs_decision" }), runReason({ status: "failed" }),
    runLine({ status: "ok", env: "dev", started_at: new Date().toISOString(), rows_written: 10, quarantined: 2, seconds: 3 }).textContent,
    runLine({ status: "ok", env: "qa", compute: "fabric", started_at: new Date().toISOString(), seconds: 1 }).textContent,
  ]);
  assert.strictEqual(out[0], "Waiting for your choice");
  assert.strictEqual(out[1], "");
  assert.match(out[2], /10 rows written, 2 quarantined/);
  assert.match(out[3], /^.*qa · Fabric · .*0 rows written/);
  assert.deepStrictEqual(app.errors, []);
});

test("Cancel run for a run Fabric no longer lists, or one that already ended, says so and cancels nothing", { skip }, async () => {
  assert.strictEqual(await page().evaluate(() => cancelFabricJob("j1")), false);
  await toastSays(/^This run isn't in the app's list of Fabric runs any more\. Cancel it from Fabric's Monitoring hub\.$/);
  await page().evaluate(() => toastClose());
  // fabric_runs, as monitor_service lists them.
  runs = [{ job_id: "j2", name: "Nightly load", kind: "Pipeline", status: "Completed", workspace_name: "Sales Dev", workspace_id: "w1", item_id: "i1" }];
  assert.strictEqual(await page().evaluate(() => cancelFabricJob("j2")), false);
  await toastSays(/^Nightly load already ended in Fabric \(Completed\)\.$/);
  assert.strictEqual(app.calls.filter((c) => c.method === "cancel_fabric_run").length, 0);
  assert.deepStrictEqual(app.errors, []);
});

test("the environment picker changes the environment the Data mesh pages use", { skip }, async () => {
  const env = await page().evaluate(() => {
    const before = { view: state.view, project: state.meta.project, env: state.meta.env };
    state.meta.project = { path: "C:/work/sales", environments: { dev: { target: "local" } } };
    state.view = "metadata";
    renderEnvBar();
    const select = document.querySelector("#env-slot .env-select");
    state.view = "home";  // the change re-renders the page: Home needs nothing from the engine
    select.value = "prod";
    select.dispatchEvent(new Event("change"));
    const now = state.meta.env;
    Object.assign(state.meta, { project: before.project });
    setGlobalEnv(before.env);
    return now;
  });
  assert.strictEqual(env, "prod");
  assert.deepStrictEqual(app.errors, []);
});

// Pop-out windows (popout.js) in the main window: Keep on top read when storage is blocked, a window the shell
// couldn't open, the band's Show window, the shell's pop-out events for a section that isn't one and for one
// opening and closing, a Fabric link sent from a pop-out, Keep on top's switch when storage is blocked, and the
// runs list's refresh holding off on another page or under a dialog. The shell is the harness's; pop_out_open
// is made to fail by the test, and nothing opens a real window.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const shellCalls = (cmd) => page().evaluate((c) => window.__fdShellCalls.filter((x) => x.cmd === c).map((x) => x.args), cmd);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {} });
  await page().waitForFunction(() => typeof popOut === "function" && window.__fdShellCalls.some((c) => c.cmd === "pop_out_list"));
});
after(async () => { await app?.close(); });
beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => { closeDialog(); toastClose(); popOuts.clear(); window.__fdShellCalls.length = 0; });
  app.errors.length = 0;
});

test("Keep on top reads as off when storage is blocked, and a window the shell couldn't open says so", { skip }, async () => {
  const out = await page().evaluate(async () => {
    const get = Storage.prototype.getItem;
    Storage.prototype.getItem = () => { throw new Error("blocked"); };
    let top;
    try { top = popOutOnTop("runs"); } finally { Storage.prototype.getItem = get; }
    const core = window.__TAURI__.core;
    const real = core.invoke;
    core.invoke = async (cmd, args) => { if (cmd === "pop_out_open") throw new Error("The window couldn't be made"); return real(cmd, args); };
    try { await popOut("runs"); } finally { core.invoke = real; }
    return { top, open: popOuts.has("runs"), toast: document.getElementById("toast").textContent };
  });
  assert.strictEqual(out.top, false);
  assert.strictEqual(out.open, false);
  assert.match(out.toast, /^Could not open the window/);
  assert.deepStrictEqual(app.errors, []);
});

test("the band's Show window opens it; the shell's events add and remove sections, and pass a Fabric link on", { skip }, async () => {
  await page().evaluate(() => {
    const host = document.createElement("div");
    host.id = "band-host";
    host.append(poppedOutBand("runs", "From KratosForge", "monitor-runs"));
    document.body.append(host);
  });
  await page().locator("#band-host").getByRole("button", { name: "Show window" }).click();
  await page().waitForFunction(() => popOuts.has("runs"));
  assert.deepStrictEqual(await shellCalls("pop_out_open"), [{ section: "runs", onTop: false }]);
  await page().evaluate(() => document.getElementById("band-host").remove());

  const out = await page().evaluate(() => {
    window.__opened = [];
    const open = openInFabric;
    openInFabric = (u) => window.__opened.push(u);
    try {
      popOuts.clear();
      window.__fdEmit("pop-out", { section: "elsewhere", open: true });
      window.__fdEmit("pop-out", { section: "fabric", open: true });
      const added = popOuts.has("fabric") && !popOuts.has("elsewhere");
      window.__fdEmit("pop-out", { section: "fabric", open: false });
      window.__fdEmit("pop-out-open-in-fabric", "https://app.fabric.microsoft.com/monitoring");
      window.__fdEmit("pop-out-open-in-fabric", { not: "a link" });
      return { added, removed: !popOuts.has("fabric"), opened: window.__opened };
    } finally { openInFabric = open; }
  });
  assert.deepStrictEqual(out, { added: true, removed: true, opened: ["https://app.fabric.microsoft.com/monitoring"] });
  assert.deepStrictEqual(app.errors, []);
});

test("Keep on top's switch works when storage is blocked; the refresh waits for Monitor's runs and no dialog", { skip }, async () => {
  const out = await page().evaluate(() => {
    const controls = popOutControls();
    const box = controls.querySelector("input");
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = () => { throw new Error("blocked"); };
    try { box.checked = true; box.dispatchEvent(new Event("change")); } finally { Storage.prototype.setItem = set; }
    const was = { view: state.view, panel: monitorPage.panel, refreshing: monitorPage.refreshing };
    monitorPage.refreshing = false;
    state.view = "home";
    popOutRefresh();
    const offPage = monitorPage.refreshing;
    state.view = "monitor";
    monitorPage.panel = "runs";
    showDialog("Something else", document.createElement("div"), []);
    popOutRefresh();
    const underDialog = monitorPage.refreshing;
    closeDialog();
    Object.assign(monitorPage, { panel: was.panel, refreshing: was.refreshing });
    state.view = was.view;
    return { offPage, underDialog, buttons: [...controls.querySelectorAll("button")].map((b) => b.textContent.trim()) };
  });
  assert.deepStrictEqual(out, { offPage: false, underDialog: false, buttons: ["Dock back"] });
  assert.deepStrictEqual(await shellCalls("pop_out_on_top"), [{ on: true }]);
  assert.deepStrictEqual(app.errors, []);
});

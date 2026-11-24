// The Sessions dialog (sessions.js) beyond sessions.test.cjs: notebooks sharing the engine's one kernel
// with no session yet, the engine's notes, Stop on the open notebook's own session, and Share refused
// while the other notebook runs a cell, then confirmed. kernel_sessions and its actions are mocked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const now = Math.floor(Date.now() / 1000);
const session = (notebook, status, pid, extra = {}) => ({ notebook, status, process: true, pid, memory_bytes: 50 * 1024 * 1024,
  cells_run: 3, last_used: now - 60, active: false, ...extra });

let sessions;
before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }),
    kernel_sessions: () => JSON.parse(JSON.stringify(sessions)),
    stop_kernel_process: () => ({ stopped: true }),
    kernel_session_share: ({ host }) => ({ host }),
  } });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => {
    document.getElementById("dialog-root").replaceChildren();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    openNotebooks.clear();
    loadIntoState({ name: "Other", cells: [] });
    loadIntoState({ name: "Daily", cells: [] });
    go("notebook");
  });
});

const dialog = () => page().locator("#dialog-root .dialog");
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);
const openSessions = async () => {
  await page().getByRole("button", { name: /^Sessions/ }).click();
  await dialog().locator(".sessions-body").waitFor();
};

test("in the engine's one kernel with nothing run yet, Sessions says so and shows the engine's notes", { skip }, async () => {
  sessions = { process: false, sessions: [], notes: ["The engine restarted 2 minutes ago."], shared: {} };
  await openSessions();
  await toastSays("The engine restarted 2 minutes ago.");
  assert.match(await dialog().innerText(), /Notebooks share one kernel inside the app's engine, so one cell runs at a time\./);
  await dialog().getByText("No sessions yet. A session starts with a notebook's first cell.").waitFor();
  await dialog().getByRole("button", { name: "Settings" }).click();
  await page().waitForFunction(() => state.view === "settings");
});

test("Stop on the open notebook's session ends it and marks the notebook stopped", { skip }, async () => {
  sessions = { process: true, max_sessions: 4, idle_minutes: 0, notes: [], shared: {},
    sessions: [session("Daily", "idle", 4242, { active: true }), session("Other", "running", 4343)] };
  await openSessions();
  assert.match(await dialog().innerText(), /At most 4 keep a process; idle ones are kept until you stop them\./);
  const row = dialog().locator(".sessions tbody tr", { hasText: "Daily" });
  assert.strictEqual(await row.getByRole("button", { name: "Open" }).count(), 0, "it's the open notebook");
  await row.getByRole("button", { name: "Stop" }).click();
  await toastSays("Stopped Daily's session. Its variables were cleared.");
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "stop_kernel_process").at(-1).params, { notebook: "Daily" });
  assert.strictEqual(await page().evaluate(() => openNotebooks.get("Daily").kernelStatus), "stopped");
  await dialog().locator(".sessions").waitFor();  // the list is read again
});

test("Share waits while the other notebook runs a cell, then shares after a yes", { skip }, async () => {
  sessions = { process: true, max_sessions: 5, idle_minutes: 30, notes: [], shared: {},
    sessions: [session("Daily", "idle", 4242, { active: true }), session("Other", "running", 4343)] };
  await page().evaluate(() => { openNotebooks.get("Other").kernelStatus = "busy"; });
  await openSessions();
  const pick = dialog().getByLabel("Notebook to share this session with");
  assert.deepStrictEqual(await pick.locator("option").allInnerTexts(), ["Other (its variables are cleared)"]);
  await dialog().getByRole("button", { name: "Share", exact: true }).click();
  await toastSays("A cell is running in Other. Share when it has finished.");
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "kernel_session_share"), []);

  await page().evaluate(() => { openNotebooks.get("Other").kernelStatus = "idle"; });
  await dialog().getByRole("button", { name: "Share", exact: true }).click();
  const confirm = page().locator("#dialog-root .dialog", { hasText: "Share Daily's session with Other?" });
  await confirm.getByRole("button", { name: "Share" }).click();
  await toastSays("Other now shares Daily's session. Its variables were cleared.");
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "kernel_session_share").at(-1).params, { notebook: "Other", host: "Daily" });
});

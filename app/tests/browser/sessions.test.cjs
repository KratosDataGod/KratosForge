// Notebook sessions (kernel_sessions.py): the Sessions dialog against the real engine, with
// notebooks in their own process. Each notebook that ran a cell has a process of its own; Stop
// ends one notebook's process and leaves the other's. No lakehouse is attached.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const ENGINE = ["app_settings", "save_app_settings", "start_cell", "poll_cell", "kernel_sessions",
  "stop_kernel_process", "kernel_variables", "kernel_info", "new_notebook", "open_notebook"];

let app;
let opened;  // the engine names the new notebook
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE, mocks: { save_notebook: () => ({ saved: true }) } });
  opened = await page().evaluate(async () => {
    state.settings = await rpc("save_app_settings", { changes: { kernel_process: true } });
    const run = async (notebook, code) => {
      let snap = await rpc("start_cell", { code, notebook });
      while (snap.status === "running") snap = await rpc("poll_cell", { version: snap.version, wait: 0.4, notebook });
    };
    await run("Other", "y = 2");
    const { name } = await rpc("new_notebook");
    await run(name, "x = 1");
    loadIntoState(await rpc("open_notebook", { name }));  // the open notebook, for the engine too
    go("notebook");
    return name;
  });
});

after(async () => {
  if (!app) return;
  await page().evaluate((opened) => Promise.all(["Other", opened].map((notebook) =>
    rpc("stop_kernel_process", { notebook }).catch(() => null))), opened);
  await app.close();
});

const row = (name) => page().locator(".sessions tbody tr", { hasText: name });

test("Sessions lists each notebook's own process, and the open one is marked", { skip }, async () => {
  await page().getByRole("button", { name: /^Sessions/ }).click();
  await page().locator(".sessions").waitFor();
  assert.match(await page().locator(".sessions-body").innerText(), /Each notebook runs in a process of its own/);
  const pids = [];
  for (const name of [opened, "Other"]) {
    const text = await row(name).innerText();
    pids.push(/Process (\d+)/.exec(text)?.[1]);
    assert.match(text, /Idle/, name);
  }
  assert.ok(pids[0] && pids[1] && pids[0] !== pids[1], `one process each: ${pids}`);
  assert.match(await row(opened).innerText(), /\(open\)/);
  assert.doesNotMatch(await row("Other").innerText(), /\(open\)/);
  assert.strictEqual(await row("Other").getByRole("button", { name: "Open" }).count(), 1);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sessions.png") });
});

test("Stop ends one notebook's process and leaves the other's variables", { skip }, async () => {
  await row("Other").getByRole("button", { name: /Stop/ }).click();
  await page().getByText("Stopped Other's session. Its variables were cleared.").waitFor();
  await page().locator(".sessions").waitFor();
  assert.doesNotMatch(await row("Other").innerText(), /Process \d+/);
  assert.match(await row(opened).innerText(), /Process \d+/);
  const x = await page().evaluate(async (notebook) => (await rpc("kernel_variables", { notebook }))
    .find((v) => v.name === "x")?.value, opened);
  assert.strictEqual(x, "1");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

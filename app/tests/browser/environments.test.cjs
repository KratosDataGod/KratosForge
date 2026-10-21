// Notebook environments (environments.py): the Environment dialog against the real engine, with
// notebooks in their own process. New makes one and attaches it, Install asks first (Cancel here,
// so pip never runs), and Delete asks as a danger and puts the notebook back on the app's packages.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const ENGINE = ["app_settings", "save_app_settings", "notebook_environments", "environment_create",
  "environment_attach", "environment_delete", "environment_install", "kernel_sessions"];

let app;
const page = () => app.page;
const dialog = () => page().locator(".dialog");

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE, mocks: { save_notebook: () => ({ saved: true }) } });
  await page().evaluate(async () => {
    state.settings = await rpc("save_app_settings", { changes: { kernel_process: true } });
    loadIntoState({ name: "Daily sales", cells: [{ type: "code", cell_id: "c1", source: "x = 1", meta: {} }] });
    go("notebook");
  });
});

after(async () => { if (app) await app.close(); });

test("New makes an environment and this notebook uses it", { skip }, async () => {
  await page().getByRole("button", { name: /^Environment/ }).click();
  assert.strictEqual(await dialog().getByLabel("Environment").inputValue(), "");
  await dialog().getByRole("button", { name: /New/ }).click();
  await page().fill(".dialog input", "Data science");
  await page().click(".dialog-actions .tool.primary");
  await dialog().getByText("No packages yet.").waitFor();
  assert.strictEqual(await dialog().getByLabel("Environment").inputValue(), "Data science");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "environment.png") });
});

test("Install asks first, and Cancel installs nothing", { skip }, async () => {
  await page().fill('.dialog input[aria-label="Packages to install"]', "scikit-learn==1.5.2");
  await dialog().getByRole("button", { name: /Install/ }).click();
  await dialog().getByText("Install into Data science?").waitFor();
  assert.match(await dialog().innerText(), /install only ones you trust/);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await dialog().getByText("No packages yet.").waitFor();
  const envs = await page().evaluate(async () => (await rpc("notebook_environments", { notebook: "Daily sales" })).environments);
  assert.deepStrictEqual(envs.map((e) => [e.name, e.packages]), [["Data science", []]]);
});

test("Delete asks as a danger and the notebook goes back to the app's packages", { skip }, async () => {
  await dialog().getByRole("button", { name: /Delete environment/ }).click();
  await dialog().getByText("Delete Data science?").waitFor();
  assert.match(await dialog().innerText(), /Daily sales go back to the app's packages/);
  assert.strictEqual(await dialog().locator(".dialog-actions .danger-solid").count(), 1);
  await dialog().locator(".dialog-actions .danger-solid").click();
  await dialog().getByText("Packages this notebook adds").waitFor();
  assert.strictEqual(await dialog().getByLabel("Environment").inputValue(), "");
  const data = await page().evaluate(async () => rpc("notebook_environments", { notebook: "Daily sales" }));
  assert.deepStrictEqual([data.environments, data.current], [[], null]);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

// OneLake writes (lakehouse.write_table): each notebook chooses under Runs on whether its table
// writes are kept here and uploaded in the background or go straight to OneLake, and Settings >
// Advanced holds the default for notebooks that don't choose, compression and skip unchanged.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const ENGINE = ["app_settings", "save_app_settings"];

let app;
const saved = [];
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE, mocks: {
    save_notebook: () => ({ saved: true }),
    notebook_onelake_writes_set: (p) => {
      saved.push(p);
      return { onelake_writes: p.mode, effective: p.mode || "local_first" };
    },
  } });
  await page().evaluate(async () => {
    await loadSettings();
    loadIntoState({ name: "TPC-H data", cells: [{ type: "code", cell_id: "c1", source: "x = 1", meta: {} }] });
    go("notebook");
  });
});

after(async () => { if (app) await app.close(); });

test("Runs on offers the notebook's own OneLake writes choice", { skip }, async () => {
  await page().getByRole("button", { name: /^Runs on/ }).click();
  const menu = page().locator("#menu");
  await menu.getByText("Table writes to OneLake from this PC.").waitFor();
  assert.match(await menu.innerText(), /Same as Settings \(Write here, then upload\)/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "onelake-writes-menu.png") });
  await menu.getByRole("menuitem", { name: /Straight to OneLake/ }).click();
  await page().getByText("TPC-H data now writes straight to OneLake.", { exact: false }).waitFor();
  assert.deepStrictEqual(saved.at(-1), { name: "TPC-H data", mode: "direct" });
  assert.strictEqual(await page().evaluate(() => state.notebook.onelake_writes), "direct");
});

test("Settings > Advanced holds the defaults", { skip }, async () => {
  await page().evaluate(() => go("settings"));
  await page().getByRole("button", { name: /^Advanced/ }).click();
  const writes = page().getByLabel("New notebooks write");
  await writes.waitFor();
  assert.strictEqual(await writes.inputValue(), "local_first");
  assert.strictEqual(await page().getByLabel("Compression").inputValue(), "zstd");
  await writes.selectOption("direct");
  await page().waitForFunction(() => state.settings?.onelake_writes?.value === "direct");
  if (SHOTS) await page().locator(".advanced-group", { hasText: "OneLake writes" }).screenshot({ path: path.join(SHOTS, "onelake-writes-settings.png") });
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

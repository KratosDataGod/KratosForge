// Settings > Appearance > Skin (skins.js), against the real engine's settings and skin list: choosing
// a skin changes the colors and the rail's order at once, keeps the open notebook and its unsaved edits,
// calls nothing but the settings save, is kept for the next start, and switching back undoes it.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, startupReads } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings", "save_app_settings", "skins_list"], viewport: { width: 1440, height: 900 },
    mocks: { readiness_check: () => [] } });
  await startupReads(app);  // the startup reads (git_info and the rest) are done before anything is counted
  await page().evaluate(() => loadSettings());
  await page().waitForFunction(() => (skinState.list || []).length >= 2);
});
after(async () => { if (app) await app.close(); });

const accent = () => page().evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--accent").trim());
const railIds = () => page().evaluate(() => railFeatures().map((f) => f.id));
const chooser = () => page().getByRole("group", { name: /^Skin/ });

test("the KratosForge skin is the default and sets no colors of its own", { skip }, async () => {
  await page().evaluate(() => go("settings"));
  await chooser().getByRole("radio", { name: "KratosForge" }).waitFor();
  assert.strictEqual(await chooser().getByRole("radio", { name: "KratosForge" }).isChecked(), true);
  assert.strictEqual(await page().evaluate(() => document.body.dataset.skin), "kratosforge");
  assert.strictEqual(await page().evaluate(() => document.documentElement.style.getPropertyValue("--accent")), "");
  assert.strictEqual(await accent(), "#1b3664");
});

test("choosing Fabric-familiar recolors and reorders the rail, keeps the open notebook, and only saves the setting", { skip }, async () => {
  await page().evaluate(() => {
    loadIntoState({ name: "Orders", cells: [{ type: "code", cell_id: "c1", source: "a = 1", meta: {} }] });
    state.notebook.cells[0].source = "a = 2  # not saved yet";
    state.dirty = true;
    go("settings");
  });
  // The Settings page reads its own cards when it opens, and again when it redraws after the save.
  await chooser().getByRole("radio", { name: "Fabric-familiar" }).waitFor();
  for (let n = -1; n !== app.calls.length; await new Promise((r) => setTimeout(r, 300))) n = app.calls.length;
  // Everything the app has read so far, as it started and opened Settings: a redraw may read these again.
  const pageReads = new Set(app.calls.map((c) => c.method));
  const before = app.calls.length;
  await chooser().getByRole("radio", { name: "Fabric-familiar" }).check();
  await page().waitForFunction(() => document.body.dataset.skin === "fabric");
  assert.strictEqual(await accent(), "#0f6c5c");
  const rail = await railIds();
  assert.deepStrictEqual(rail.slice(0, 3), ["home", "workspaces", "onelake"]);
  assert.ok(rail.length <= 8, `the rail holds at most 8 pages (${rail.length})`);
  const made = app.calls.slice(before).map((c) => c.method);
  // The switch itself saves the setting and nothing else: any other call is the Settings page redrawing.
  assert.ok(made.includes("save_app_settings"), made.join(", "));
  assert.deepStrictEqual(made.filter((m) => m !== "save_app_settings" && m !== "app_settings" && !pageReads.has(m)), [], made.join(", "));
  assert.deepStrictEqual(await page().evaluate(() => [state.notebook.name, state.notebook.cells[0].source, state.dirty]),
    ["Orders", "a = 2  # not saved yet", true]);
  if (SHOTS) {
    await chooser().scrollIntoViewIfNeeded();
    await page().screenshot({ path: path.join(SHOTS, "skin-fabric-settings.png") });
  }
});

test("the chosen skin is kept for the next start", { skip }, async () => {
  await page().reload();
  await page().waitForFunction(() => typeof loadSettings === "function");
  await page().evaluate(() => loadSettings());
  await page().waitForFunction(() => document.body.dataset.skin === "fabric");
  assert.strictEqual(await accent(), "#0f6c5c");
});

test("switching back to KratosForge removes the skin's colors and restores the rail", { skip }, async () => {
  await page().evaluate(() => go("settings"));
  await chooser().getByRole("radio", { name: "KratosForge" }).check();
  await page().waitForFunction(() => document.body.dataset.skin === "kratosforge");
  assert.strictEqual(await page().evaluate(() => document.documentElement.style.getPropertyValue("--accent")), "");
  assert.strictEqual(await accent(), "#1b3664");
  assert.deepStrictEqual((await railIds()).slice(0, 2), ["home", "create"]);
  assert.deepStrictEqual(app.errors, []);
});

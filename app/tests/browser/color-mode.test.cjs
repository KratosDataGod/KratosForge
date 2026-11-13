// Settings > Appearance > Color mode (ui_color_mode, skins.js colorMode) against the real engine's settings
// and skins: light by default, a user's Dark only while Preview features are shown, a policy value always
// and locked, a skin's own dark tokens when it has them, and every page readable in dark (axe).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const mode = () => page().evaluate(() => document.documentElement.dataset.colorMode);
const token = (name) => page().evaluate((name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim(), name);
const set = (value, source = "user", preview = true) => page().evaluate(([value, source, preview]) => {
  state.settings.ui_color_mode = { value, source };
  state.settings.show_preview = { ...(state.settings.show_preview || {}), value: preview };
  applySkin();
}, [value, source, preview]);
const skin = (id) => page().evaluate((id) => { state.settings.ui_skin = { ...(state.settings.ui_skin || {}), value: id }; applySkin(); }, id);

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings", "save_app_settings", "skins_list"], viewport: { width: 1440, height: 900 },
    mocks: { readiness_check: () => [], xray: () => ({ findings: [], scanned_at: null }) } });
  await startupReads(app);
  await page().evaluate(() => loadSettings());
  await page().waitForFunction(() => (skinState.list || []).length >= 2);
});
after(async () => { if (app) await app.close(); });

test("KratosForge is light by default, and choosing Dark in Settings turns the app dark and is saved", { skip }, async () => {
  assert.strictEqual(await mode(), "light");
  assert.strictEqual(await token("--surface"), "#ffffff");
  await page().evaluate(() => go("settings"));
  const choice = page().getByRole("group", { name: /^Color mode/ });
  await choice.getByRole("button", { name: "Dark" }).click();
  await page().waitForFunction(() => document.documentElement.dataset.colorMode === "dark");
  assert.strictEqual(await token("--surface"), "#252526");
  assert.strictEqual(await page().evaluate(() => loadSettings().then((s) => s.ui_color_mode.value)), "dark");
  await choice.getByRole("button", { name: "Follow the skin" }).click();
  await page().waitForFunction(() => document.documentElement.dataset.colorMode === "light");
});

test("in dark the header shows the name as text and leaves the brand lettering image as it is", { skip }, async () => {
  const shown = () => page().evaluate(() => [".brand-name", ".brand-text"].map((s) => getComputedStyle(document.querySelector(s)).display !== "none"));
  assert.deepStrictEqual(await shown(), [true, false]);
  await set("dark");
  assert.deepStrictEqual(await shown(), [false, true]);
  assert.strictEqual(await page().locator(".brand-name").getAttribute("src"), "kratosforge-lettering.png");
  assert.strictEqual(await page().getByText("KratosForge", { exact: true }).first().isVisible(), true);
  await set("skin", "default", true);
});

test("a user's Dark waits for Preview features; a policy value applies anyway", { skip }, async () => {
  await set("dark", "user", false);
  assert.strictEqual(await mode(), "light");
  await set("dark", "policy", false);
  assert.strictEqual(await mode(), "dark");
  await set("light", "policy", true);
  assert.strictEqual(await mode(), "light");
  await set("skin", "default", true);
});

test("a policy value shows locked in Settings, even with Preview features hidden", { skip }, async () => {
  await page().evaluate(() => {
    const load = loadSettings;
    window.loadSettings = async () => {
      const s = await load();
      if (s) { s.ui_color_mode = { value: "dark", source: "policy" }; s.show_preview = { ...s.show_preview, value: false }; }
      return s;
    };
    go("settings");
  });
  const choice = page().getByRole("group", { name: /^Color mode/ });
  await choice.waitFor();
  assert.strictEqual(await choice.getByRole("button", { name: "Dark" }).isDisabled(), true);
  assert.strictEqual(await choice.getByRole("button", { name: "Dark" }).getAttribute("aria-pressed"), "true");
  assert.match(await page().locator(".motion-settings").textContent(), /Set by your organization's policy/);
  await page().evaluate(() => { delete window.loadSettings; });
  await page().evaluate(() => loadSettings());
  await set("skin", "default", true);
});

test("in dark a skin uses its own dark colors, or KratosForge's when it has none", { skip }, async () => {
  await set("dark");
  await skin("databricks");
  assert.strictEqual(await token("--surface"), "#23272e");
  await skin("fabric");
  assert.strictEqual(await token("--surface"), "#252526");
  assert.strictEqual(await token("--accent"), "#8fb4f0", "Fabric's light teal doesn't carry into dark");
  await skin("kratosforge");
  await set("skin", "default", true);
});

test("every main page passes axe in dark", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await set("dark");
  const found = {};
  for (const view of ["home", "create", "workspaces", "onelake", "metadata", "monitor", "optimize", "platform", "learn", "settings"]) {
    await page().evaluate((view) => go(view), view);
    await page().waitForTimeout(400);
    const f = await axeFindings(page(), "body");
    if (f.length) found[view] = f;
  }
  await page().evaluate(() => {
    loadIntoState({ name: "Orders", cells: [{ type: "markdown", cell_id: "m", source: "# Orders to Silver", meta: {} },
      { type: "code", cell_id: "c1", source: "df = spark.read.table('bronze.orders')\n# count by region\ndf.groupBy('region').count()", meta: {} }] });
    state.ribbonTab = "home"; go("notebook");
  });
  await page().waitForTimeout(400);
  const nb = await axeFindings(page(), "body");
  if (nb.length) found.notebook = nb;
  await set("skin", "default", true);
  assert.deepStrictEqual(found, {});
});

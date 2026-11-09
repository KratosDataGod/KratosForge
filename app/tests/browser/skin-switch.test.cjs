// Settings > Appearance > Skin (skins.js), against the real engine's settings and skin list: choosing
// a skin changes the colors and the rail's order at once, keeps the open notebook and its unsaved edits,
// calls nothing but the settings save, is kept for the next start, and switching back undoes it.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

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
  assert.deepStrictEqual(rail.slice(0, 5), ["home", "create", "workspaces", "onelake", "monitor"]);  // mockup v7
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

test("the skin cards have no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  assert.deepStrictEqual(await axeFindings(page(), ".skin-chooser"), []);
});

test("a save that fails leaves the skin in force checked", { skip }, async () => {
  await page().evaluate(() => { window.__realSave = saveSettings; saveSettings = async () => false; });
  await chooser().getByRole("radio", { name: "KratosForge" }).click();
  await page().waitForFunction(() => document.querySelector('input[name="ui_skin"][value="fabric"]')?.checked);
  assert.strictEqual(await page().evaluate(() => document.body.dataset.skin), "fabric");
  await page().evaluate(() => { saveSettings = window.__realSave; });
});

test("the chosen skin is kept for the next start", { skip }, async () => {
  await page().reload();
  await page().waitForFunction(() => typeof loadSettings === "function");
  await page().evaluate(() => loadSettings());
  await page().waitForFunction(() => document.body.dataset.skin === "fabric");
  assert.strictEqual(await accent(), "#0f6c5c");
});

test("a platform skin groups KratosForge's own pages under a label, and keeps where code runs on the toolbar", { skip }, async () => {
  const rail = await page().evaluate(() => [...document.getElementById("rail-main").children]
    .map((el) => el.classList.contains("rail-group-label") ? `label:${el.textContent}` : el.textContent.trim()));
  const at = rail.indexOf("label:KratosForge");
  assert.ok(at > 0, rail.join(", "));
  assert.deepStrictEqual(rail.slice(at + 1), ["Platform", "Optimize", "Data mesh"]);
  const size = await page().$eval(".rail-group-label", (el) => parseFloat(getComputedStyle(el).fontSize));
  assert.ok(size >= 11, `the label is at least 11px (${size})`);  // UX, mockup v7
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#rail-main"), []);
  // The skin never hides or changes where code runs: the same chip as in the KratosForge skin.
  await page().evaluate(() => { loadIntoState({ name: "Where", cells: [{ type: "code", cell_id: "c1", source: "1", meta: {} }] }); go("notebook"); });
  await page().locator(".engine-where").first().waitFor();
  assert.strictEqual(await page().locator(".engine-where").first().textContent(), "This PC · no Fabric job");
  assert.ok(await page().locator(".engine-where").first().isVisible());
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "skin-fabric-notebook.png") });
});

test("switching back to KratosForge removes the skin's colors and restores the rail", { skip }, async () => {
  await page().evaluate(() => go("settings"));
  await chooser().getByRole("radio", { name: "KratosForge" }).check();
  await page().waitForFunction(() => document.body.dataset.skin === "kratosforge");
  assert.strictEqual(await page().evaluate(() => document.documentElement.style.getPropertyValue("--accent")), "");
  assert.strictEqual(await accent(), "#1b3664");
  assert.deepStrictEqual((await railIds()).slice(0, 3), ["home", "create", "workspaces"]);
  assert.strictEqual(await page().locator(".rail-group-label").count(), 0);  // the KratosForge skin needs no label
  assert.deepStrictEqual(app.errors, []);
});

test("each skin marks the page with its look, part by part, and KratosForge keeps its own", { skip }, async () => {
  const look = (skin) => page().evaluate((skin) => {
    state.settings.ui_skin = { ...(state.settings.ui_skin || {}), value: skin };
    applySkin();
    const d = document.body.dataset;
    return [d.skinFont, d.skinHeader, d.skinRail, d.skinCorners, d.skinTabs, d.skinCells, d.skinButtons, d.skinTiles, d.skinIcons];
  }, skin);
  assert.deepStrictEqual(await look("databricks"), ["dm-sans", "light", "inline", "square", "boxed", "flat", "accent", "plain", "bold"]);
  assert.deepStrictEqual(await look("snowflake"), ["inter", "none", "inline", "round", "pill", "rounded", "accent", "plain", "regular"]);
  assert.deepStrictEqual(await look("fabric"), ["segoe", "brand", "stacked", "soft", "underline", "card", "accent", "plain", "light"]);
  // Back to KratosForge, every part is its own again: nothing from the last skin stays.
  assert.deepStrictEqual(await look("kratosforge"), ["segoe-variable", "light", "stacked", "soft", "underline", "bordered", "accent", "plain", "regular"]);
  assert.deepStrictEqual(app.errors, []);
});

test("a skin's typeface loads from the app's own fonts and its corners change, under the app's CSP", { skip }, async () => {
  const drawn = (skin, family) => page().evaluate(async ({ skin, family }) => {
    state.settings.ui_skin = { ...(state.settings.ui_skin || {}), value: skin };
    applySkin();
    if (family) await document.fonts.load(`600 14px "${family}"`);
    const cs = getComputedStyle(document.body);
    return { font: cs.fontFamily.split(",")[0].replace(/"/g, "").trim(), loaded: family ? document.fonts.check(`600 14px "${family}"`) : true,
      corner: cs.getPropertyValue("--r-sm").trim() };
  }, { skin, family });
  assert.deepStrictEqual(await drawn("databricks", "DM Sans"), { font: "DM Sans", loaded: true, corner: "2px" });
  assert.deepStrictEqual(await drawn("snowflake", "Inter"), { font: "Inter", loaded: true, corner: "6px" });
  assert.deepStrictEqual(await drawn("fabric"), { font: "Segoe UI", loaded: true, corner: "4px" });
  assert.deepStrictEqual(await drawn("kratosforge"), { font: "Segoe UI Variable Text", loaded: true, corner: "4px" });
  assert.deepStrictEqual(app.errors, []);
});

test("a wide rail puts each label beside its icon, and gives its room back in a narrow window", { skip }, async () => {
  // The page settles its layout a frame or two after a skin or window change, so wait for the width asked.
  const railAt = (w) => page().waitForFunction((w) => Math.round(document.getElementById("rail").getBoundingClientRect().width) === w, w, { timeout: 3000 }).catch(() => {});
  const rail = () => page().evaluate(() => {
    const r = document.getElementById("rail").getBoundingClientRect();
    const item = getComputedStyle(document.querySelector(".rail-item"));
    return { width: Math.round(r.width), direction: item.flexDirection };
  });
  await page().evaluate(() => { state.settings.ui_skin = { ...(state.settings.ui_skin || {}), value: "databricks" }; applySkin(); });
  await railAt(208);
  assert.deepStrictEqual(await rail(), { width: 208, direction: "row" });
  await page().setViewportSize({ width: 900, height: 900 });
  await railAt(72);
  assert.deepStrictEqual(await rail(), { width: 72, direction: "column" });
  // A short window (200% zoom on a laptop): like the app's own rail, it shows icons only.
  await page().setViewportSize({ width: 1280, height: 600 });
  await railAt(52);
  assert.deepStrictEqual(await rail(), { width: 52, direction: "column" });
  await page().setViewportSize({ width: 1440, height: 900 });
  await page().evaluate(() => { state.settings.ui_skin.value = "kratosforge"; applySkin(); });
  await railAt(72);
  assert.deepStrictEqual(await rail(), { width: 72, direction: "column" });
});

test("a brand header fills the bar with the skin's accent and keeps its text readable", { skip }, async () => {
  const bar = () => page().evaluate(() => {
    const cs = (sel) => getComputedStyle(document.querySelector(sel));
    return { bg: cs("body > header.topbar").backgroundColor, search: cs("body > header.topbar .search input").color, n: document.querySelectorAll(".topbar").length };
  });
  await page().evaluate(() => { state.settings.ui_skin = { ...(state.settings.ui_skin || {}), value: "fabric" }; applySkin(); });
  await page().waitForFunction(() => getComputedStyle(document.querySelector("body > header.topbar")).backgroundColor === "rgb(15, 108, 92)", null, { timeout: 3000 }).catch(() => {});
  assert.deepStrictEqual(await bar(), { bg: "rgb(15, 108, 92)", search: "rgb(255, 255, 255)", n: 1 });
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".topbar"), []);
  await page().evaluate(() => { state.settings.ui_skin.value = "kratosforge"; applySkin(); });
  await page().waitForFunction(() => getComputedStyle(document.querySelector("body > header.topbar")).backgroundColor === "rgb(255, 255, 255)", null, { timeout: 3000 }).catch(() => {});
  assert.strictEqual((await bar()).bg, "rgb(255, 255, 255)");
});

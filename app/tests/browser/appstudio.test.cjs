// App Studio (appstudio.js, AS-07/08) with the real engine: Create > Applications opens it, a template makes a
// project on this PC, the preview is labelled as sample data and a slicer narrows it, Design changes the app
// with Undo and Redo, versions restore, and Publish changes nothing. Also axe on the start view and workspace.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const ENGINE = ["app_settings", "save_app_settings", "appstudio_home", "appstudio_create", "appstudio_open", "appstudio_change",
  "appstudio_undo", "appstudio_redo", "appstudio_history", "appstudio_versions", "appstudio_save_version",
  "appstudio_restore_version", "appstudio_save_as", "appstudio_export", "appstudio_import", "appstudio_preview"];

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE });
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
});

after(async () => { await app?.close(); });

test("Create > Applications opens App Studio without a rail item of its own", { skip }, async () => {
  const rail = await page().locator("#rail-main .rail-item").allTextContents();
  assert.ok(!rail.some((t) => /App Studio/.test(t)) && rail.length <= 8);
  await page().locator("#rail-main .rail-item", { hasText: "Create" }).click();
  await page().locator(".tile", { hasText: "App Studio" }).click();
  await page().locator(".as-create").waitFor();
  assert.match(await page().locator(".page.appstudio h1").textContent(), /App Studio\s*Preview/);
  assert.match(await page().locator(".as-projects").textContent(), /No apps yet/);
  assert.strictEqual(await page().locator(".rail-item.active").textContent(), "Create");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "appstudio-start.png") });
});

test("App Studio's start view has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  assert.deepStrictEqual(await axeFindings(page(), "#view"), []);
});

test("a template makes a project and the preview is sample data a slicer narrows", { skip }, async () => {
  await page().locator(".as-create input[aria-label='App name']").fill("Sales overview");
  await page().locator(".as-template", { hasText: "Sales overview" }).click();
  await page().locator(".as-create button", { hasText: "Create" }).click();
  await page().locator(".as-grid").waitFor();
  assert.match(await page().locator(".as-sim").textContent(), /Local preview on sample data/);
  assert.match(await page().locator(".as-chips").textContent(), /Saved[\s\S]*Valid[\s\S]*Not published/);
  assert.deepStrictEqual((await page().locator(".as-page").allTextContents()).map((t) => t.replace(/\s*\d+$/, "")), ["Overview", "Products"]);
  const before = await page().locator(".as-t-card .as-card-value").first().textContent();
  assert.strictEqual(await page().locator(".as-t-bar svg rect").count(), 5);
  await page().locator(".as-t-slicer select").selectOption("East");
  await page().waitForFunction(() => document.querySelectorAll(".as-t-bar svg rect").length === 1);
  assert.notStrictEqual(await page().locator(".as-t-card .as-card-value").first().textContent(), before);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "appstudio-preview.png") });
  assert.deepStrictEqual(app.errors, []);
});

test("the workspace has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  assert.deepStrictEqual(await axeFindings(page(), "#view"), []);
});

test("Design changes the app, and Undo and Redo step through it", { skip }, async () => {
  await page().locator(".subtabs [role=tab]", { hasText: "Design" }).click();
  const title = page().locator("input[aria-label='Title of c_rev']");
  await title.fill("Total revenue");
  await title.press("Tab");
  await page().waitForFunction(() => appStudio.opened.spec.pages[0].components[0].title === "Total revenue");
  await page().locator("select[aria-label='Type of c_region']").selectOption("column");
  await page().waitForFunction(() => appStudio.opened.spec.pages[0].components[4].type === "column");
  await page().locator("select[aria-label='Component to add']").selectOption("text");
  await page().locator("button", { hasText: "Add component" }).click();
  await page().waitForFunction(() => appStudio.opened.spec.pages[0].components.length === 7);
  await page().locator(".head-actions button", { hasText: "Undo" }).click();
  await page().waitForFunction(() => appStudio.opened.spec.pages[0].components.length === 6);
  await page().locator(".head-actions button", { hasText: "Redo" }).click();
  await page().waitForFunction(() => appStudio.opened.spec.pages[0].components.length === 7);
  await page().locator(".subtabs [role=tab]", { hasText: "Spec" }).click();
  assert.match(await page().locator(".as-json").textContent(), /"title": "Total revenue"/);
});

test("a saved version restores, keeping what was there as a new version", { skip }, async () => {
  await page().locator(".head-actions button", { hasText: "Save version" }).click();
  await page().locator(".dialog input").fill("Before trim");
  await page().locator(".dialog button", { hasText: "Save version" }).click();
  await page().waitForFunction(() => document.getElementById("toast")?.textContent.includes("Saved version 1"));
  await page().evaluate(() => appStudioChange([{ op: "remove", id: "c_month" }], "Trim"));
  await page().locator(".subtabs [role=tab]", { hasText: "History" }).click();
  await page().locator("button[aria-label='Restore version 1']").click();
  await page().locator(".dialog button", { hasText: "Restore" }).click();
  await page().waitForFunction(() => appStudio.opened.spec.pages[0].components.some((c) => c.id === "c_month"));
  await page().locator("button[aria-label='Restore version 2']").waitFor();
  assert.match(await page().locator(".as-changes").textContent(), /Restored version 1/);
});

test("Publish says what it would create and changes nothing", { skip }, async () => {
  const before = app.calls.length;
  await page().locator(".head-actions button", { hasText: "Publish" }).click();
  const dialog = page().locator(".dialog");
  assert.match(await dialog.textContent(), /Power BI report[\s\S]*Sales overview: 2 pages[\s\S]*Connect a semantic model/);
  assert.ok(await dialog.locator(".dialog-actions button", { hasText: "Publish" }).isDisabled());
  await dialog.locator("button", { hasText: "Close" }).click();
  assert.deepStrictEqual(app.calls.slice(before).map((c) => c.method).filter((m) => m.startsWith("appstudio_")), []);
});

test("the project is listed again when App Studio opens", { skip }, async () => {
  await page().locator(".page-head a", { hasText: "App Studio" }).click();
  await page().locator(".as-projects button", { hasText: "Open" }).waitFor();
  assert.match(await page().locator(".as-projects").textContent(), /Sales overview/);
});

// Opening a warehouse: the Fabric portal tab in full screen, Open in KratosForge from it, and the
// "Open warehouses in" setting (see harness.cjs for how to run it). Fabric and the shell are mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const WS = "d64d74d5-58ab-4882-aedc-a7d2b835b094";
const WH = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const LH = "11111111-2222-3333-4444-555555555555";
const EP = "66666666-7777-8888-9999-000000000000";

let app;
let settings;
const page = () => app.page;
const shellCalls = (cmd) => page().evaluate((c) => window.__fdShellCalls.filter((x) => x.cmd === c).map((x) => x.args), cmd);

before(async () => {
  if (skip) return;
  settings = { wh_open_in: { value: "kratosforge", source: "default" }, fabric_full_screen: { value: false, source: "default" } };
  app = await openApp({
    mocks: {
      app_settings: () => settings,
      save_app_settings: ({ changes }) => { for (const [k, v] of Object.entries(changes)) settings[k] = { value: v, source: "user" }; return settings; },
      list_workspaces: () => [{ id: WS, displayName: "Adv Wrks DE 2 Dev" }],
      list_items: () => [{ id: WH, type: "Warehouse", displayName: "AdventureWorks_Warehouse" },
        { id: LH, type: "Lakehouse", displayName: "AdventureWorksLakehouse" },
        { id: EP, type: "SQLEndpoint", displayName: "AdventureWorksLakehouse" }],
      meta_project: () => ({ environments: { dev: { workspace_id: WS } } }),
      workspace_marks: () => ({ favorites: [], pins: [] }),
    },
  });
  await page().evaluate(() => { state.user = "test@contoso.com"; return loadSettings(); });
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

async function openWorkspace() {
  await page().evaluate(() => go("workspaces"));
  await page().locator("tr", { hasText: "Adv Wrks DE 2 Dev" }).getByRole("button", { name: /Show items|Hide items/ }).waitFor();
  if (!(await page().evaluate((ws) => workspacesPage.open.has(ws), WS))) {
    await page().locator("tr", { hasText: "Adv Wrks DE 2 Dev" }).getByRole("button", { name: "Show items" }).click();
  }
  await page().getByRole("button", { name: "AdventureWorks_Warehouse" }).waitFor();
}

test("by default a warehouse's name opens the Warehouse page, and Open in Fabric stays", { skip }, async () => {
  await openWorkspace();
  const name = page().getByRole("button", { name: "AdventureWorks_Warehouse" });
  assert.strictEqual(await name.getAttribute("title"), "Open on the Warehouse page");
  const row = page().locator(".ws-items tr", { has: name });
  assert.strictEqual(await row.getByRole("button", { name: "SQL query" }).count(), 0, "the name does what SQL query did");
  assert.strictEqual(await row.getByRole("button", { name: "Open in Fabric" }).count(), 1);
  await name.click();
  await page().waitForFunction(() => state.view === "query" && sqlPage.ref?.item_id === "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
});

test("with Fabric portal chosen, a warehouse's name opens Fabric's editor", { skip }, async () => {
  await page().evaluate(() => saveSettings({ wh_open_in: "fabric" }));
  await openWorkspace();
  await page().getByRole("button", { name: "AdventureWorks_Warehouse" }).click();
  await page().waitForFunction(() => state.view === "fabric" && fabricPage.page.includes("/datawarehouses/"));
  await page().evaluate(() => saveSettings({ wh_open_in: "kratosforge" }));
});

test("Full screen hides the app's chrome, keeps a way out, and is remembered", { skip }, async () => {
  await page().evaluate(() => go("fabric"));
  await page().waitForSelector(".fabric-page .fabric-slot");
  const before = await page().$eval(".fabric-slot", (e) => e.getBoundingClientRect().toJSON());
  await page().getByRole("button", { name: "Full screen" }).click();
  await page().waitForSelector("body.fabric-full");
  assert.ok(!(await page().isVisible(".rail")) && !(await page().isVisible(".topbar")), "the rail and top bar are hidden");
  const after = await page().$eval(".fabric-slot", (e) => e.getBoundingClientRect().toJSON());
  assert.ok(after.width > before.width && after.height > before.height, "Fabric gets the room");
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "fabric_view_show" && c.args.width > 1300));
  assert.strictEqual(settings.fabric_full_screen.value, true);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "fabric-full-screen.png") });
  await page().evaluate(() => go("home"));
  assert.ok(!(await page().evaluate(() => document.body.classList.contains("fabric-full"))), "only the Fabric tab is full screen");
  await page().evaluate(() => go("fabric"));
  await page().waitForSelector("body.fabric-full");
  await page().keyboard.press("Escape");
  await page().waitForFunction(() => !document.body.classList.contains("fabric-full"));
  assert.strictEqual(settings.fabric_full_screen.value, false);
  await page().getByRole("button", { name: "Full screen" }).click();
  await page().getByRole("button", { name: "Exit full screen" }).click();
  await page().waitForFunction(() => !document.body.classList.contains("fabric-full"));
});

async function fabricShows(address) {
  await page().evaluate((a) => {
    const invoke = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = (cmd, args) => (cmd === "fabric_view_url" ? Promise.resolve(a) : invoke(cmd, args));
    window.__fdRestoreInvoke = () => { window.__TAURI__.core.invoke = invoke; };
  }, address);
}

test("Open in KratosForge opens the warehouse or endpoint Fabric shows on the Warehouse page", { skip }, async () => {
  await page().evaluate(() => go("fabric"));
  await fabricShows(`https://app.fabric.microsoft.com/groups/${WS.toUpperCase()}/datawarehouses/${WH}`);
  await page().getByRole("button", { name: "Open in KratosForge" }).click();
  await page().waitForFunction((id) => state.view === "query" && sqlPage.ref?.item_id === id, WH);
  assert.deepStrictEqual(await page().evaluate(() => [sqlPage.ref.kind, sqlPage.ref.workspace_name]), ["warehouse", "Adv Wrks DE 2 Dev"]);
  await page().evaluate(() => window.__fdRestoreInvoke());
  await page().evaluate(() => go("fabric"));
  await fabricShows(`https://app.fabric.microsoft.com/groups/${WS}/lakewarehouses/${EP}`);
  await page().getByRole("button", { name: "Open in KratosForge" }).click();
  await page().waitForFunction((id) => state.view === "query" && sqlPage.ref?.kind === "endpoint" && sqlPage.ref.item_id === id, LH);
  await page().evaluate(() => window.__fdRestoreInvoke());
});

test("on any other Fabric page Open in KratosForge says what it needs and opens nothing", { skip }, async () => {
  await page().evaluate(() => go("fabric"));
  for (const address of [`https://app.fabric.microsoft.com/groups/${WS}/lakehouses/${LH}`, null,
    `https://evil.example/groups/${WS}/datawarehouses/${WH}`]) {
    await fabricShows(address);
    await page().getByRole("button", { name: "Open in KratosForge" }).click();
    await page().waitForFunction(() => /Open a warehouse or SQL analytics endpoint in Fabric first/.test(document.querySelector(".toast")?.textContent || ""));
    assert.strictEqual(await page().evaluate(() => state.view), "fabric");
    await page().evaluate(() => { window.__fdRestoreInvoke(); document.getElementById("toast").textContent = ""; });
  }
});

test("with preview features hidden there's no Open in KratosForge and a name opens Fabric", { skip }, async () => {
  settings.show_preview = { value: false, source: "user" };
  await page().evaluate(() => loadSettings());
  await page().evaluate(() => go("fabric"));
  await page().waitForSelector(".fabric-page .fabric-slot");
  assert.strictEqual(await page().getByRole("button", { name: /Open in KratosForge/ }).count(), 0);
  await openWorkspace();
  assert.strictEqual(await page().getByRole("button", { name: "AdventureWorks_Warehouse" }).getAttribute("title"), "Open in Fabric");
  delete settings.show_preview;
  await page().evaluate(() => loadSettings());
});

test("Settings > Advanced has the choice, saved when picked", { skip }, async () => {
  await page().evaluate(() => {
    const s = { ...state.settings, sql_temp_dir: { value: "" }, sql_warehouse_writes: { value: true }, wh_monitoring_auto: { value: [] } };
    for (const [key] of SQL_SETTINGS) s[key] = { value: 1 };
    const box = sqlSettingsSection(s, () => false);
    box.id = "open-in-settings";
    document.getElementById("view").replaceChildren(box);
  });
  const group = page().getByRole("radiogroup", { name: "Open warehouses in" });
  assert.ok(await group.getByRole("radio", { name: /KratosForge/ }).isChecked());
  await group.getByRole("radio", { name: /Fabric portal/ }).click();  // saving draws the page again
  await page().waitForFunction(() => state.settings?.wh_open_in?.value === "fabric");
  await page().evaluate(() => saveSettings({ wh_open_in: "kratosforge" }));
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});

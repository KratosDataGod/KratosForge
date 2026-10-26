// The notebook's Home ribbon (Cleaner KratosForge): Side by side, Validate and Prove sit under Check,
// Save a copy, Versions and Export under Share, Schedule and Recent runs under Schedule. Each moved
// button keeps its Preview tag and on state inside the menu, and the Run tab still has them all.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 }, engine: ["list_notebooks", "open_notebook", "save_notebook",
    "notebook_places", "compute_check", "kernel_variables"], mocks: { notebook_runs: () => ({ runs: [] }) } });
  await page().evaluate(async () => {
    await rpc("save_notebook", { name: "Daily sales", cells: [{ type: "code", source: "orders = 1" }] });
    await openNotebook("Daily sales");
  });
});
after(async () => { if (app) await app.close(); });

const items = async (menu) => {
  await page().getByRole("button", { name: menu, exact: true }).click();
  const names = await page().locator("#menu [role^=menuitem]").allTextContents();
  await page().keyboard.press("Escape");
  return names;
};

test("Home holds one row: the moved buttons sit under Check, Share and Schedule", { skip }, async () => {
  const ribbon = await page().evaluate(() => [...document.querySelectorAll("button")].filter((b) => b.closest("#view") && !b.closest(".cells, .explorer, .nb-side, .statusbar"))
    .map((b) => b.textContent.trim()));
  for (const moved of ["Save a copy", "Versions", "Validate"]) assert.ok(!ribbon.includes(moved), `${moved} is still on Home`);
  assert.deepStrictEqual(await items("Check"), ["Side by sidePreview", "ValidatePreview", "ProvePreview"]);
  assert.deepStrictEqual(await items("Share"), ["Save a copy", "Versions", "ExportPreview"]);
  assert.deepStrictEqual(await items("Schedule"), ["SchedulePreview", "Recent runsPreview"]);
  if (SHOTS) {
    await page().getByRole("button", { name: "Check", exact: true }).click();
    await page().screenshot({ path: path.join(SHOTS, "notebook-ribbon-check.png") });
    await page().keyboard.press("Escape");
  }
});

test("a panel opened from a menu shows the menu's button as on, and the item as checked", { skip }, async () => {
  await page().getByRole("button", { name: "Check", exact: true }).click();
  await page().keyboard.press("ArrowDown");  // the first item has focus; arrows move like any menu
  const focused = await page().evaluate(() => document.activeElement.textContent);
  assert.match(focused, /^Validate/);
  await page().keyboard.press("Enter");
  await page().locator(".validatepane").waitFor();
  assert.strictEqual(await page().locator("#menu").count(), 0, "the menu closed");
  assert.match(await page().getByRole("button", { name: "Check", exact: true }).getAttribute("class"), /toggled/);
  await page().getByRole("button", { name: "Check", exact: true }).click();
  assert.strictEqual(await page().getByRole("menuitemcheckbox", { name: /^Validate/ }).getAttribute("aria-checked"), "true");
  await page().keyboard.press("Escape");
});

test("Export opens its formats from the Share button", { skip }, async () => {
  await page().getByRole("button", { name: "Share" }).click();
  await page().getByRole("menuitem", { name: /^Export/ }).click();
  await page().getByRole("menuitem", { name: "Fabric source (.py)" }).waitFor();
  const [menu, share] = await page().evaluate(() => [document.getElementById("menu").getBoundingClientRect().left,
    [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Share").getBoundingClientRect().left]);
  assert.ok(Math.abs(menu - share) < 400 && menu > 0, `the formats menu opens by Share (${menu} vs ${share})`);
  await page().keyboard.press("Escape");
});

test("the Run tab still has every moved run button one click away", { skip }, async () => {
  await page().getByRole("tab", { name: "Run" }).click();
  for (const name of [/^Side by side/, /^Validate/, /^Prove/, /^Schedule/, /^Recent runs/]) {
    assert.strictEqual(await page().getByRole("button", { name }).count(), 1, String(name));
  }
  await page().getByRole("tab", { name: "Home" }).click();
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

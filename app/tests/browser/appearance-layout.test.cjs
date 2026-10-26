// Settings > Appearance > Layout (features.js applyAppearance), against the real engine's settings:
// explanations beside the page or on request from the title bar, Preview tags or quiet dots, and
// comfortable or compact spacing. Each choice is kept for the next start; nothing is removed.
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
  app = await openApp({ engine: ["app_settings", "save_app_settings"], viewport: { width: 1440, height: 900 }, mocks: {
    workloads_list: () => ({ rows: [], groups: [], sources: [], views: [{ id: "attention", label: "Needs attention", count: 0 }],
      total: 0, offset: 0, limit: 50, user: "chris@contoso.com" }),
    readiness_check: () => [],
  } });
  await page().evaluate(() => loadSettings());
});
after(async () => { if (app) await app.close(); });

const monitor = async () => {
  await page().evaluate(() => { monitorPage.panel = "workloads"; monitorPage.showFabric = false; go("monitor"); });
  await page().waitForSelector(".mon-how .how-it-works", { state: "attached" });
};
const choose = async (group, label) => {
  await page().evaluate(() => go("settings"));
  await page().getByRole("group", { name: group }).getByRole("button", { name: label }).click();
  await page().locator(`[role="group"][aria-label="${group}"] button[aria-pressed="true"]`, { hasText: label }).waitFor();
};
const bodyHas = (cls) => page().evaluate((c) => document.body.classList.contains(c), cls);

test("new installs show everything: panels beside the page, Preview tags, comfortable spacing", { skip }, async () => {
  await monitor();
  assert.ok(await page().isVisible(".mon-how .how-it-works"));
  assert.ok(await page().isHidden("#how-toggle"), "the title bar's info button shows only with explanations on request");
  for (const cls of ["explain-on-request", "preview-dots", "density-compact"]) assert.strictEqual(await bodyHas(cls), false, cls);
  await page().evaluate(() => go("settings"));
  for (const [group, on] of [["Explanations", "Beside the page"], ["Preview marks", "Tags"], ["Density", "Comfortable"]]) {
    assert.strictEqual(await page().getByRole("group", { name: group }).getByRole("button", { name: on }).getAttribute("aria-pressed"), "true", group);
  }
});

test("explanations on request: the panel leaves the page and opens as a drawer from the title bar", { skip }, async () => {
  await choose("Explanations", "On request");
  assert.strictEqual(await page().evaluate(async () => (await rpc("app_settings")).ui_explanations.value), "on_request", "kept for the next start");
  await monitor();
  await page().waitForSelector("#how-toggle:not([hidden])");
  assert.ok(await page().isHidden(".mon-how .how-it-works"));
  const width = await page().$eval(".mon-how .with-how-main", (m) => m.getBoundingClientRect().width);
  assert.ok(width > 1200, `the list takes the panel's width (${width}px)`);
  await page().click("#how-toggle");
  await page().waitForSelector(".how-it-works.how-shown", { state: "visible" });
  assert.strictEqual(await page().getAttribute("#how-toggle", "aria-expanded"), "true");
  assert.match(await page().textContent(".how-it-works.how-shown"), /What it reads[\s\S]*What it changes[\s\S]*Where results go/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "appearance-how-drawer.png") });
  // Escape with a dialog open closes the dialog, not the drawer behind it.
  await page().evaluate(() => { confirmDialog("Keep the drawer?", "Escape closes this first."); });
  await page().locator(".overlay").waitFor();
  await page().keyboard.press("Escape");
  await page().locator(".overlay").waitFor({ state: "detached" });
  assert.ok(await page().isVisible(".how-it-works.how-shown"), "the drawer stayed open");
  await page().keyboard.press("Escape");
  await page().waitForSelector(".mon-how .how-it-works", { state: "hidden" });
  await page().click("#how-toggle");
  await page().waitForSelector(".how-it-works.how-shown", { state: "visible" });
  await page().evaluate(() => go("settings"));
  await page().waitForFunction(() => !document.body.classList.contains("how-open"));
  assert.ok(await page().isHidden("#how-toggle"), "Settings has no panel, so no button");
});

test("Preview marks as dots keep the word for screen readers, and page titles keep the tag", { skip }, async () => {
  await choose("Preview marks", "Quiet dots");
  await page().evaluate(() => { setView(h("div", { class: "page" }, h("h1", {}, "Pipelines", previewTag()),
    h("div", { class: "subtabs" }, h("button", { class: "tab" }, "Runs", previewTag())))); });
  const [title, tab] = await page().$$eval(".preview-tag", (tags) => tags.map((t) => [t.getBoundingClientRect().width, t.textContent]));
  assert.ok(title[0] > 30 && title[1] === "Preview", `the title keeps its tag (${title})`);
  assert.ok(tab[0] <= 8 && tab[1] === "Preview", `the tab shows a dot with the word kept (${tab})`);
});

test("compact density tightens table rows, and Beside the page brings the panels back", { skip }, async () => {
  await choose("Density", "Compact");
  const padding = await page().evaluate(() => {
    setView(h("table", { class: "list" }, h("tbody", {}, h("tr", {}, h("td", {}, "x")))));
    return getComputedStyle(document.querySelector(".list td")).paddingTop;
  });
  assert.strictEqual(padding, "5px");
  await choose("Explanations", "Beside the page");
  await monitor();
  assert.ok(await page().isVisible(".mon-how .how-it-works"));
  assert.ok(await page().isHidden("#how-toggle"));
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

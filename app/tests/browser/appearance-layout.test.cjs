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

test("new installs fold help away: the info button opens it, with Preview tags and comfortable spacing", { skip }, async () => {
  await monitor();
  assert.ok(await page().isHidden(".mon-how .how-it-works"), "the panel waits behind the info button");
  await page().waitForSelector("#how-toggle:not([hidden])");
  assert.strictEqual(await bodyHas("explain-on-request"), true);
  for (const cls of ["preview-dots", "density-compact"]) assert.strictEqual(await bodyHas(cls), false, cls);
  await page().evaluate(() => go("settings"));
  for (const [group, on] of [["Explanations", "On request"], ["Preview marks", "Tags"], ["Density", "Comfortable"]]) {
    assert.strictEqual(await page().getByRole("group", { name: group }).getByRole("button", { name: on }).getAttribute("aria-pressed"), "true", group);
  }
});

test("explanations on request: the panel leaves the page and opens as a drawer from the title bar", { skip }, async () => {
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
});

test("a page's explanation line folds into the drawer; a page with only that line gets an About drawer", { skip }, async () => {
  await page().evaluate(() => { state.settings.ui_explanations = { value: "on_request" }; applyAppearance();
    window.__learned = 0;
    setView(h("div", { class: "page" }, h("h1", {}, "Sample"),
      pageIntro("What this page is for. ", h("a", { class: "learn-more", href: "#", onclick: (e) => { e.preventDefault(); window.__learned++; } }, "Learn more")),
      h("p", { class: "sub" }, "3 items need you."))); });
  assert.ok(await page().isHidden(".page-intro"), "the explanation leaves the page");
  assert.ok(await page().isVisible("p.sub:not(.page-intro)"), "facts stay on the page");
  await page().waitForSelector("#how-toggle:not([hidden])");
  await page().click("#how-toggle");
  const drawer = page().locator(".how-it-works.how-shown");
  await drawer.waitFor({ state: "visible" });
  assert.match(await drawer.textContent(), /About this page[\s\S]*What this page is for\./);
  await drawer.getByRole("link", { name: "Learn more" }).click();
  assert.strictEqual(await page().evaluate(() => window.__learned), 1, "the page's own Learn more still works from the drawer");
  await page().keyboard.press("Escape");
  // A page with a panel shows the line above it, and the page keeps no copy once help sits beside the page again.
  await page().evaluate(() => setView(h("div", { class: "page" }, h("h1", {}, "Sample"), pageIntro("Why you would come here."),
    withHowItWorks(h("div", {}, "list"), howItWorks("sample", { reads: "a", changes: "b", results: "c" })))));
  await page().click("#how-toggle");
  await drawer.waitFor({ state: "visible" });
  assert.match(await drawer.textContent(), /^\s*Why you would come here\./);
  await page().keyboard.press("Escape");
  await choose("Explanations", "Beside the page");
  await page().evaluate(() => setView(h("div", { class: "page" }, h("h1", {}, "Sample"), pageIntro("Shown on the page."))));
  assert.ok(await page().isVisible(".page-intro"));
  assert.strictEqual(await page().locator("#view .how-intro-only").count(), 0);
  assert.ok(await page().isHidden("#how-toggle"));
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

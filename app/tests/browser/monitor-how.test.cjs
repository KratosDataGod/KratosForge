// Testing feedback item 18: Monitor > KratosForge > Operations > Workloads and Agents say how they work beside their list
// (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    workloads_list: () => ({ rows: [], groups: [], sources: [], views: [{ id: "attention", label: "Needs attention", count: 0 }],
      total: 0, offset: 0, limit: 50, user: "chris@contoso.com" }),
    agents_list: () => ({ agents: [], envs: [] }),
    readiness_check: () => [],
  } });
  // These check the panel beside the page, the layout people get from Settings > Appearance > Explanations.
  await app.page.evaluate(() => { state.settings = { ...(state.settings || {}), ui_explanations: { value: "beside" } }; applyAppearance(); });
});
after(async () => { if (app) await app.close(); });

const open = async (panel) => {
  await page().evaluate((p) => { monitorPage.panel = p; monitorPage.showFabric = false; go("monitor"); }, panel);
  await page().waitForSelector(".mon-how .how-it-works");
};

for (const [panel, words] of [["workloads", "workloads.sqlite"], ["agents", "never reaches the agent's server"]]) {
  test(`Monitor > ${panel} says how it works, beside its list`, { skip }, async () => {
    await open(panel);
    const text = await page().textContent(".how-it-works");
    for (const part of ["What it reads", "What it changes", "Where results go", words, "Learn more"]) assert.ok(text.includes(part), part);
    const side = await page().$eval(".with-how", (w) => {
      const [main, how] = [w.querySelector(".with-how-main"), w.querySelector(".how-it-works")];
      return how.getBoundingClientRect().left >= main.getBoundingClientRect().right && main.scrollWidth <= main.clientWidth;
    });
    assert.ok(side, "the panel sits on the right and the list fits");
    if (SHOTS) await page().screenshot({ path: path.join(SHOTS, `monitor-${panel}-how.png`) });
  });
}

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

// #1681: every left-rail page reflows (WCAG 1.4.10) and stays usable zoomed to 200%, with the mock engine
// (see harness.cjs for how to run it).
//
// - 320 x 640: the reflow width, a 1280 px window at 400%. 640 x 470: the default 1280 x 940 window at 200%.
// - At each size, on each rail page, the window and the page don't scroll sideways, and nothing a person can
//   see sits past the right edge. A data table may scroll inside its own box, which reflow allows for content
//   that needs two dimensions; a row of tabs, buttons or text may not.
// - No page is left out: the rail pages walked are exactly the features with a rail entry (features.js), as in
//   keyboard-pass.test.cjs, so a new page is checked the day it is registered.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, animationsDone } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const SIZES = [{ name: "320 px (reflow)", width: 320, height: 640 }, { name: "640 px (200%)", width: 640, height: 470 }];
const walked = new Map(SIZES.map((s) => [s.name, []]));

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: SIZES[0].width, height: SIZES[0].height },
    // show_preview defaults to true (sidecar/fabric_desktop/app_settings.py DEFAULTS), so preview pages are on the rail.
    engine: ["app_settings", "git_info"],
    mocks: {
      environment: () => ({ python: "Python 3 (test)", user: "ana@contoso.com", tenant: "t1" }),
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev", capacityId: "c1" }, { id: "w2", displayName: "Finance", capacityId: null }],
      workspace_marks: () => ({ favorites: [], pins: [] }),
      pipeline_list: () => ({ pipelines: [{ path: "C:/repos/sales/Load orders.DataPipeline", repo: "sales", relative: "Load orders.DataPipeline",
        name: "Load orders", activity_count: 3, last_run: null }], truncated: false }),
      platform_domains: () => [{ id: "d1", name: "Sales", parent_id: null }],
      platform_products: () => ({ products: [] }),
    },
  });
  await page().waitForSelector("#rail-main .rail-item");
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
});

after(async () => { await app?.close(); });

// Until the page has made no engine call for 300 ms (at most 4 s) and nothing is still fading in.
async function settle() {
  let last = -1;
  for (let t = 0; t < 4000; t += 300) {
    if (app.calls.length === last) break;
    last = app.calls.length;
    await page().waitForTimeout(300);
  }
  await animationsDone(page());
}

// Runs in the page: what scrolls sideways, and what can be seen past the window's right edge.
function overflow() {
  const width = document.documentElement.clientWidth;
  const name = (el) => `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}${[...el.classList].map((c) => `.${c}`).join("")}`;
  const label = (el) => (el.getAttribute("aria-label") || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 30);
  // A box of its own that a data table, code or an editor may scroll sideways in.
  const tableBox = (el) => el.matches("table, pre, .cm-scroller") || !!el.querySelector(":scope > table");
  const problems = [];
  if (document.documentElement.scrollWidth > width + 1) problems.push(`the window scrolls sideways (${document.documentElement.scrollWidth} px wide)`);
  const sideways = new Set();
  for (const el of document.querySelectorAll("header.topbar, #rail, #view, header.topbar *, #rail *, #view *")) {
    const style = getComputedStyle(el);
    if (/(auto|scroll)/.test(style.overflowX) && el.scrollWidth > el.clientWidth + 1 && !tableBox(el)) {
      sideways.add(el);
      problems.push(`${name(el)} scrolls sideways (${el.scrollWidth} px in ${el.clientWidth})`);
    }
  }
  for (const el of document.querySelectorAll("header.topbar *, #rail *, #view *")) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || getComputedStyle(el).visibility === "hidden" || el.closest(".sr-only")) continue;
    // Inside a box that scrolls (a table's, or one already reported), it's reached by scrolling that box.
    let box = el.parentElement;
    while (box && !(/(auto|scroll)/.test(getComputedStyle(box).overflowX) && box.scrollWidth > box.clientWidth + 1)) box = box.parentElement;
    if (box && (tableBox(box) || sideways.has(box))) continue;
    if (r.right <= width + 1 && r.left >= -1) continue;
    // The innermost one only: its parents are past the edge because it is.
    if ([...el.children].some((c) => c.getBoundingClientRect().right > width + 1)) continue;
    problems.push(`${name(el)} "${label(el)}" runs to ${Math.round(r.right)} px`);
  }
  return [...new Set(problems)].slice(0, 12);
}

for (const size of SIZES) {
  test(`every rail page at ${size.name}: no sideways scroll, nothing past the right edge`, { skip }, async (t) => {
    await page().setViewportSize({ width: size.width, height: size.height });
    const rail = await page().evaluate(() => [...railFeatures(), ...railFeatures(true)]
      .map((f) => ({ id: f.id, name: f.rail.label, view: Object.keys(f.views || {})[0] })));
    for (const r of rail) {
      await t.test(r.name, async () => {
        await page().evaluate((view) => { closeDialog(); go(view); }, r.view);
        await settle();
        walked.get(size.name).push(r.id);
        const problems = await page().evaluate(overflow);
        assert.deepStrictEqual(problems, [], `${r.name} at ${size.name}: ${problems.join("; ")}`);
      });
    }
    assert.deepStrictEqual(app.errors, []);
  });
}

test("the reflow check walked every page with a rail entry at every size", { skip }, async () => {
  const registered = await page().evaluate(() => features.list.filter((f) => f.rail).map((f) => f.id));
  for (const [name, ids] of walked) assert.deepStrictEqual([...ids].sort(), [...registered].sort(), `a rail page not checked at ${name}`);
  assert.ok(registered.length >= 11, `only ${registered.length} rail pages registered`);
});

test("the reflow check catches a row that runs past the edge", { skip }, async () => {
  await page().setViewportSize({ width: 320, height: 640 });
  await page().evaluate(() => { closeDialog(); go("settings"); });
  await settle();
  await page().evaluate(() => document.querySelector("#view .page").prepend(
    h("div", { id: "rf-row", style: "display: flex; white-space: nowrap" }, "A long line of words that will not wrap at this width at all")));
  const problems = await page().evaluate(overflow);
  assert.ok(problems.some((p) => /rf-row|scrolls sideways/.test(p)), problems.join("; ") || "nothing reported");
  await page().evaluate(() => document.getElementById("rf-row").remove());
});

// Accessibility in every skin (the skins brief: accessible keyboard flows and reduced motion in every
// skin; docs/skins.md). A skin changes two things: the colors, through tokens, and the rail's order and
// labels. Each built-in skin is checked here for both:
// - axe: every rail page has no serious or critical WCAG 2.1 AA findings, color contrast included,
//   because the colors are the skin's.
// - keyboard: Tab goes through the rail in the order it is drawn. The "KratosForge" group label is a
//   heading, never a Tab stop. Enter on a rail item opens its page.
// - reduced motion: with the system asking for less motion, switching skins starts no animation or
//   transition that lasts longer than the stylesheet's 0.01 ms floor.
// keyboard-pass.test.cjs walks every page's whole window in the default skin. A skin doesn't change
// a page's controls, so that pass isn't repeated per skin.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, animationsDone, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SKINS = ["kratosforge", "fabric", "databricks", "snowflake"];
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "save_app_settings", "skins_list", "git_info"],
    mocks: {
      readiness_check: () => [],
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev", capacityId: "c1" }],
      pipeline_list: () => ({ pipelines: [], truncated: false }),
      platform_domains: () => [{ id: "d1", name: "Sales", parent_id: null }],
      platform_products: () => ({ products: [] }),
    },
  });
  await page().emulateMedia({ reducedMotion: "reduce" });
  await page().evaluate(() => loadSettings());
  await page().waitForFunction(() => (skinState.list || []).length >= 4);
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

async function useSkin(id) {
  await page().evaluate(async (id) => { await saveSettings({ ui_skin: id }); render(); }, id);
  await page().waitForFunction((id) => document.body.dataset.skin === id, id);
}

test("the four built-in skins are there to check", { skip }, async () => {
  assert.deepStrictEqual(await page().evaluate(() => skinState.list.map((s) => s.id)).then((l) => [...l].sort()), [...SKINS].sort());
});

for (const skin of SKINS) {
  test(`${skin}: the rail is a keyboard path in its drawn order`, { skip }, async () => {
    await useSkin(skin);
    await page().evaluate(() => go("home"));
    await settle();
    const drawn = await page().evaluate(() => [...document.querySelectorAll("#rail .rail-item")].map((b) => b.textContent.trim()));
    // From the first rail item, Tab visits every rail item in drawn order.
    await page().locator("#rail .rail-item").first().focus();
    const visited = [];
    for (let i = 0; i < drawn.length; i++) {
      const at = await page().evaluate(() => {
        const el = document.activeElement;
        return el?.closest("#rail") && el.classList.contains("rail-item") ? el.textContent.trim() : `(${el?.tagName} ${el?.className})`;
      });
      visited.push(at);
      await page().keyboard.press("Tab");
    }
    assert.deepStrictEqual(visited, drawn);
    const label = await page().evaluate(() => [...document.querySelectorAll(".rail-group-label")]
      .map((el) => ({ tabbable: el.tabIndex >= 0, role: el.getAttribute("role") })));
    for (const l of label) assert.deepStrictEqual(l, { tabbable: false, role: "heading" });
    // Enter on a rail item opens its page.
    const second = page().locator("#rail-main .rail-item").nth(1);
    await second.focus();
    await page().keyboard.press("Enter");
    await page().waitForFunction(() => document.querySelector("#rail-main .rail-item.active") === document.querySelectorAll("#rail-main .rail-item")[1]);
  });

  test(`${skin}: no serious axe findings on any rail page`, { skip: skip || (!axeSource && "axe-core is not installed") }, async (t) => {
    await useSkin(skin);
    const views = await page().evaluate(() => [...railFeatures(), ...railFeatures(true)]
      .map((f) => ({ name: f.rail.label, view: Object.keys(f.views || {})[0] })));
    for (const v of views) {
      await t.test(v.name, async () => {
        await page().evaluate((view) => { closeDialog(); go(view); }, v.view);
        await settle();
        assert.deepStrictEqual(await axeFindings(page(), "body"), [], `${skin} / ${v.name}`);
      });
    }
  });
}

test("with reduced motion asked for, switching skins starts no animation or transition a person could see", { skip }, async () => {
  await page().evaluate(() => go("home"));
  await settle();
  // The stylesheet's reduced-motion rule cuts every transition and animation to 0.01 ms (styles.css), so
  // the browser still reports them. What counts is anything that would last longer than that.
  await page().evaluate(() => {
    const ms = (v) => Math.max(...v.split(",").map((x) => (x.trim().endsWith("ms") ? parseFloat(x) : parseFloat(x) * 1000)));
    window.__skinMotion = [];
    document.addEventListener("transitionrun", (e) => {
      const d = ms(getComputedStyle(e.target).transitionDuration);
      if (d > 0.01) window.__skinMotion.push(`${e.propertyName} ${d} ms on ${e.target.tagName.toLowerCase()}.${e.target.className}`);
    });
  });
  assert.strictEqual(await page().evaluate(() => document.documentElement.dataset.motionOverride), undefined);
  for (const skin of ["fabric", "databricks", "snowflake", "kratosforge"]) {
    await useSkin(skin);
    const moving = await page().evaluate(() => [...window.__skinMotion.splice(0),
      ...document.getAnimations().filter((a) => a.playState === "running" && (a.effect?.getComputedTiming().duration || 0) > 0.01)
        .map((a) => a.animationName || a.transitionProperty || "animation")]);
    assert.deepStrictEqual(moving, [], `${skin}: ${moving.join(", ")}`);
  }
  assert.deepStrictEqual(app.errors, []);
});

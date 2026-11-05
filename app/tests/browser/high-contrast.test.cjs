// Windows High Contrast (#955): under forced colors the system repaints every color and drops
// backgrounds and box-shadows. The selected rail item, tab, segment and notebook cell, and a focused button, still
// have to stand out from the ones around them. The signed-out avatar's text reaches 4.5:1 normally.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 }, mocks: { readiness_check: () => [], save_notebook: () => ({ saved: true }) } });
});
after(async () => { if (app) await app.close(); });

// What a contrast theme still draws: colors, borders and outline. Font weight alone doesn't count, and
// a see-through background is the page's own.
const looks = (sel) => page().evaluate((s) => [...document.querySelectorAll(s)].map((n) => {
  const c = getComputedStyle(n);
  const seeThrough = /rgba\(.*,\s*0\)$|transparent/.test(c.backgroundColor);
  const bg = seeThrough ? getComputedStyle(document.body).backgroundColor : c.backgroundColor;
  return [bg, c.color, `${c.borderBottomStyle} ${c.borderBottomWidth} ${c.borderBottomColor}`,
    `${c.outlineStyle} ${c.outlineWidth} ${c.outlineColor}`].join(" | ");
}), sel);

async function standsOut(on, others, what) {
  const [mine] = await looks(on);
  assert.ok(mine, `${what}: no selected item found`);
  const rest = await looks(others);
  assert.ok(rest.length, `${what}: no other items found`);
  for (const other of rest) assert.notStrictEqual(other, mine, `${what} looks the same as the ones around it: ${mine}`);
}

test("selected and focused items stand out under forced colors", { skip }, async () => {
  await page().emulateMedia({ forcedColors: "active" });
  try {
    await page().evaluate(() => { go("monitor"); });
    await page().locator(".monitor-tabs .tab.active").waitFor();
    assert.ok(await page().evaluate(() => matchMedia("(forced-colors: active)").matches));
    await standsOut("#rail .rail-item.active", "#rail .rail-item:not(.active)", "the current page in the rail");
    await standsOut(".monitor-tabs .tab.active", ".monitor-tabs .tab:not(.active)", "the selected tab");
    await standsOut(".monitor-pages button.on", ".monitor-pages button:not(.on)", "the selected segment");
    // A highlighted item draws its own text colors: the theme's backplate behind its text would
    // otherwise hide the text against the highlight.
    for (const sel of ["#rail .rail-item.active", ".monitor-tabs .tab.active", ".monitor-pages button.on"]) {
      assert.strictEqual(await page().locator(sel).evaluate((n) => getComputedStyle(n).forcedColorAdjust), "none", sel);
    }
    const button = page().locator(".monitor-pages button:not(.on)").first();
    await button.focus();
    await page().keyboard.press("Shift+Tab");
    await page().keyboard.press("Tab");
    const ring = await page().evaluate(() => {
      const c = getComputedStyle(document.activeElement);
      return { style: c.outlineStyle, width: parseFloat(c.outlineWidth), color: c.outlineColor, bg: getComputedStyle(document.body).backgroundColor };
    });
    assert.notStrictEqual(ring.style, "none", "a focused button shows an outline");
    assert.ok(ring.width >= 2, `focus outline is ${ring.width}px`);
    assert.notStrictEqual(ring.color, ring.bg, "the outline isn't the page's own color");
  } finally {
    await page().emulateMedia({ forcedColors: "none" });
  }
  assert.deepStrictEqual(app.errors, []);
});

test("the selected notebook cell stands out under forced colors", { skip }, async () => {
  await page().evaluate(() => {
    loadIntoState({ name: "Contrast", cells: [{ type: "code", cell_id: "a", source: "x = 1", meta: {} },
      { type: "code", cell_id: "b", source: "y = 2", meta: {} }] });
    go("notebook");
  });
  await page().locator(".cell").nth(1).waitFor();
  await page().locator(".cell").first().click();
  await page().keyboard.press("Escape");
  await page().locator(".cell.selected").first().waitFor();
  await page().emulateMedia({ forcedColors: "active" });
  try {
    await standsOut(".cell.selected .cell-box", ".cell:not(.selected) .cell-box", "the selected cell");
  } finally {
    await page().emulateMedia({ forcedColors: "none" });
    await page().evaluate(() => go("home"));
  }
});

test("the signed-out avatar's text reaches 4.5:1", { skip }, async () => {
  const ratio = await page().evaluate(() => {
    const a = document.querySelector(".avatar.signed-out");
    if (!a) return null;
    const rgb = (s) => s.match(/[\d.]+/g).slice(0, 3).map(Number);
    const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
      .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
    const c = getComputedStyle(a);
    const [x, y] = [lum(rgb(c.color)), lum(rgb(c.backgroundColor))];
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  });
  assert.ok(ratio !== null, "the signed-out avatar is shown");
  assert.ok(ratio >= 4.5, `contrast is ${ratio.toFixed(2)}:1`);
});

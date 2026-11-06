// Skin command placement (Preview; docs/skins.md, mockup "skin command placement"), against the real
// engine's skin list: each platform skin puts its notebook commands on the Home ribbon's left group as
// drawn, More holds every command it didn't place, a failed cell takes the primary slot without the
// group growing, a command that can't run stays in place and says why, and the command bar lists the
// skin's commands first under the skin's names. KratosForge places none, so its ribbon is today's.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const code = (id, source) => ({ type: "code", cell_id: id, source, meta: {} });

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings", "skins_list"], viewport: { width: 1440, height: 900 },
    mocks: { readiness_check: () => [] } });
  await page().evaluate(() => loadSettings());
  await page().waitForFunction(() => (skinState.list || []).length >= 4);
  // Previews on, so every notebook command (Validate, Run in Fabric, Pin) is there to place or list.
  await page().evaluate(() => { state.settings.show_preview = { ...(state.settings.show_preview || {}), value: true }; });
});
after(async () => { if (app) await app.close(); });

async function openIn(skin, cells = [code("c1", "a = 1"), code("c2", "b = a + 1")]) {
  await page().evaluate(({ skin, cells }) => {
    state.settings.ui_skin = { ...(state.settings.ui_skin || {}), value: skin };
    applySkin();
    loadIntoState({ name: "Orders", cells });
    state.ribbonTab = "home";
    go("notebook");
  }, { skin, cells });
  await page().waitForSelector(".ribbon .toolbar button");
}
// A placed command is a button, or the first button of a split (Publish keeps its chevron on the bar).
const PLACED = ".ribbon .skin-commands > button, .ribbon .skin-commands > .split > button:first-child";
const group = () => page().$$eval(PLACED, (bs) => bs.map((b) => b.textContent.trim()));
const more = async () => {
  await page().click(".ribbon .skin-commands [data-skin-more]");
  const items = await page().$$eval("#menu button", (bs) => bs.map((b) => b.textContent.trim()));
  await page().keyboard.press("Escape");
  return items;
};
const shot = async (name) => {
  if (!SHOTS) return;
  await page().mouse.move(0, 0);  // no hover in the picture
  await page().locator(".ribbon").screenshot({ path: path.join(SHOTS, `skin-commands-${name}.png`) });
};

test("KratosForge places no commands, so its Home ribbon is today's", { skip }, async () => {
  await openIn("kratosforge");
  assert.strictEqual(await page().$(".skin-commands"), null);
  assert.deepStrictEqual(await page().$$eval(".ribbon .toolbar button.primary", (bs) => bs.map((b) => b.textContent.trim())), ["Run all"]);
  assert.ok(await page().$('.ribbon .split button:has-text("Publish to OneLake")'));
  assert.ok(await page().$(".ribbon button[data-save]"));
  await shot("kratosforge");
});

for (const [skin, drawn] of [
  ["fabric", ["Run all", "Run cell", "Save", "More"]],
  ["databricks", ["Run all", "Run cell", "Clear state and run all", "Publish to OneLake", "More"]],
  ["snowflake", ["Run all", "Run", "Save", "More"]],
]) {
  test(`${skin}: the group is the skin's, More holds every other command, and the rest of the ribbon stays`, { skip }, async () => {
    await openIn(skin);
    assert.deepStrictEqual(await group(), drawn);
    assert.deepStrictEqual(await page().$$eval(".ribbon .skin-commands button.primary", (bs) => bs.map((b) => b.textContent.trim())), ["Run all"]);
    await shot(skin);
    // None can be hidden: placed and under More, the group holds every notebook command once.
    const all = await page().evaluate(() => actionsFor({ view: "notebook", nb: state.notebook, busy: false }).map((s) => s.action.id));
    const ids = await page().$$eval(PLACED, (bs) => bs.filter((b) => b.dataset.action).map((b) => b.dataset.action));
    const under = await page().evaluate(async () => {
      document.querySelector(".ribbon .skin-commands [data-skin-more]").click();
      const got = [...document.querySelectorAll("#menu [data-action]")].map((b) => b.dataset.action);
      closeMenu();
      return got;
    });
    assert.deepStrictEqual([...ids, ...under].sort(), [...all].sort());
    assert.deepStrictEqual(under, all.filter((id) => !ids.includes(id)), "More keeps the order they're defined in");
    assert.ok((await more()).includes("Run in Fabric"), "Run in Fabric keeps its own name under More");
    // Everything right of the group stays: Cancel, Runs on, Check, Add data items, Share and Schedule.
    for (const name of ["Cancel", "Check", "Add data items", "Share", "Schedule"]) {
      assert.ok(await page().$(`.ribbon .toolbar > button:has-text("${name}")`), name);
    }
    // Publish keeps its options: its chevron when placed, Change publish workspace after it under More.
    assert.strictEqual(await page().$('.ribbon .toolbar > button[aria-label="Publish options"]'), null, "no orphan chevron");
    if (ids.includes("notebook.publish")) {
      await page().click('.ribbon .skin-commands .split button[aria-label="Publish to OneLake options"]');
      assert.ok((await page().$$eval("#menu button", (bs) => bs.map((b) => b.textContent.trim()))).includes("Change workspace..."));
      await page().keyboard.press("Escape");
    } else {
      const items = await more();
      assert.strictEqual(items[items.indexOf("Publish to OneLake") + 1], "Change publish workspace...");
    }
    assert.strictEqual(await page().$('.ribbon .toolbar > button[data-save]'), null, "Save isn't on the ribbon twice");
    if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".ribbon"), []);
  });
}

test("a failed cell takes the primary slot and the skin's last button drops into More", { skip }, async () => {
  await openIn("databricks");
  await page().evaluate(() => { state.notebook.cells[1].status = "error"; refreshSkinCommandGroup(); });
  assert.deepStrictEqual(await group(), ["Go to the failed cell", "Run all", "Run cell", "Clear state and run all", "More"]);
  assert.deepStrictEqual(await page().$$eval(".ribbon .skin-commands button.primary", (bs) => bs.map((b) => b.textContent.trim())), ["Go to the failed cell"]);
  await shot("databricks-failed");
  assert.ok((await more()).includes("Publish to OneLake"));
});

test("a placed command that can't run stays in place, focusable, and says why when chosen", { skip }, async () => {
  await openIn("databricks");
  await page().evaluate(() => { state.notebook.compute = "fabric"; renderNotebook(); });
  const runCell = page().locator('.ribbon .skin-commands > button[data-action="notebook.run_cell"]');
  assert.strictEqual(await runCell.getAttribute("aria-disabled"), "true");
  assert.match(await runCell.getAttribute("title"), /Fabric runs the whole notebook/);
  assert.strictEqual(await runCell.evaluate((b) => b.disabled), false, "still focusable");
  await runCell.focus();
  assert.strictEqual(await page().evaluate(() => document.activeElement?.dataset.action), "notebook.run_cell");
  await page().keyboard.press("Enter");  // chosen from the keyboard, as a screen-reader user would
  await page().waitForFunction(() => /Run cell: Fabric runs the whole notebook/.test(document.getElementById("toast")?.textContent || ""));
  await page().evaluate(() => { state.notebook.compute = "local"; });
});

test("a keyboard user keeps their place when a run starts and ends", { skip }, async () => {
  await openIn("fabric");
  for (const sel of ['[data-action="notebook.run_cell"]', "[data-skin-more]"]) {
    await page().focus(`.ribbon .skin-commands ${sel}`);
    for (const k of ["busy", "idle"]) {
      await page().evaluate((k) => setKernel(k), k);
      assert.ok(await page().evaluate((sel) => document.activeElement?.matches(`.skin-commands ${sel}`), sel), `${sel} after ${k}`);
    }
  }
});

test("the command bar lists the skin's commands first, under its names, and still finds them by their own", { skip }, async () => {
  await openIn("databricks");
  const actions = await page().evaluate(() => paletteItems("").filter((i) => i.group === "Actions").map((i) => i.label));
  assert.deepStrictEqual(actions.slice(0, 4), ["Run all", "Run cell", "Clear state and run all", "Publish to OneLake"]);
  const found = await page().evaluate(() => paletteItems("Restart the kernel").filter((i) => i.group === "Actions").map((i) => i.label));
  assert.deepStrictEqual(found, ["Clear state and run all"]);
  // Back in KratosForge the command keeps its own name.
  await openIn("kratosforge");
  const own = await page().evaluate(() => paletteItems("Restart the kernel").filter((i) => i.group === "Actions").map((i) => i.label));
  assert.deepStrictEqual(own, ["Restart the kernel and run all"]);
  assert.deepStrictEqual(app.errors, []);
});

// LX-07: Living Experience empty states (empty-spots.js). Each spot is an inline SVG with a name, one
// sentence and one action; other skins and Preview off keep the plain empty state with the same words.
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
  app = await openApp({ engine: ["app_settings"], mocks: { list_notebooks: () => [], profile_get: () => null } });
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings && !state.starting);
});
after(async () => { if (app) await app.close(); });

const draw = (opts) => page().evaluate(({ preview, skin }) => {
  state.settings.show_preview.value = preview;
  state.settings.ui_skin = { ...(state.settings.ui_skin || { source: "user" }), value: skin };
  const host = document.createElement("div");
  host.id = "spot-host";
  document.getElementById("spot-host")?.remove();
  document.body.append(host);
  for (const spot of ["forge", "land", "tools"]) host.append(livingEmpty({ spot, fallbackIcon: "notebook", title: `T-${spot}`, text: "One sentence.", action: btn("Go", null, null, "tool primary") }));
  return { svgs: host.querySelectorAll("svg.empty-spot").length, named: [...host.querySelectorAll("svg.empty-spot")].every((s) => s.getAttribute("aria-label")),
    titles: [...host.querySelectorAll(".title")].map((t) => t.textContent), buttons: host.querySelectorAll("button").length };
}, opts);

test("the KratosForge skin with Preview on draws a named spot, a title and one action", { skip }, async () => {
  const out = await draw({ preview: true, skin: "kratosforge" });
  assert.deepStrictEqual(out, { svgs: 3, named: true, titles: ["T-forge", "T-land", "T-tools"], buttons: 3 });
  for (const spot of ["forge", "land", "tools"]) {
    const size = await page().evaluate((n) => new XMLSerializer().serializeToString(emptySpotSvg(n)).length, spot);
    assert.ok(size < 4096, `${spot} is ${size} bytes`);
  }
  if (SHOTS) await page().locator("#spot-host").screenshot({ path: path.join(SHOTS, "empty-spots.png") });
});

test("another skin or Preview off keeps the plain empty state with the same words", { skip }, async () => {
  for (const opts of [{ preview: true, skin: "databricks" }, { preview: false, skin: "kratosforge" }]) {
    const out = await draw(opts);
    assert.deepStrictEqual(out, { svgs: 0, named: true, titles: ["T-forge", "T-land", "T-tools"], buttons: 3 });
  }
});

test("Home with no notebooks shows the cold forge and New notebook", { skip }, async () => {
  await page().evaluate(() => { state.settings.show_preview.value = true; state.settings.ui_skin = { ...(state.settings.ui_skin || { source: "user" }), value: "kratosforge" }; document.getElementById("spot-host")?.remove(); homeCache.done = false; go("home"); });
  await page().getByText("The forge is cold").waitFor();
  assert.ok(await page().getByRole("img", { name: "An anvil beside a cold forge with one ember" }).isVisible());
});

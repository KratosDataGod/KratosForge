// PB-070: the command bar (Ctrl+K) driven with the keyboard in a real browser: it opens with focus in
// it, typing filters, the arrows move the choice and Enter runs it, Escape closes it and gives focus
// back, the top bar's box opens it with the key typed, and Preview commands follow Settings
// (see harness.cjs for how to run it).
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const bar = () => page().locator("#palette");
const rows = () => page().locator("#palette .palette-item").allTextContents();
const chosen = () => page().locator("#palette .palette-item.on").textContent();

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings"], mocks: { list_notebooks: () => [{ name: "Sales daily" }, { name: "Orders" }] } });
  await page().waitForSelector("#rail-main .rail-item");
  // Settings come from the engine; the Preview test reads them, so they must be in first.
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
});

after(async () => { await app?.close(); });

// Each test starts on Home with nothing open, whatever the one before left behind.
beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => { document.getElementById("palette")?.remove(); closeDialog(); state.query = ""; syncSearchBox(); go("home"); });
});

async function closed() {
  if (await bar().count()) await page().keyboard.press("Escape");
  assert.strictEqual(await bar().count(), 0);
}

test("Ctrl+K opens it with focus in its box, and typing narrows the list", { skip }, async () => {
  await page().keyboard.press("Control+k");
  await bar().waitFor();
  assert.strictEqual(await page().evaluate(() => document.activeElement?.getAttribute("aria-label")), "Command bar");
  const all = (await rows()).length;
  await page().keyboard.type("monitor");
  const some = await rows();
  assert.ok(some.length > 0 && some.length < all, `${some.length} of ${all} rows after typing`);
  // A row matches by its label or its keywords (Runs is a Monitor command), never everything.
  assert.deepStrictEqual(some, await page().evaluate(() => paletteItems("monitor").map((i) => i.label)));
  assert.ok(some.includes("Monitor") && !some.includes("Home"), some.join(" | "));
  await page().keyboard.type("zzqq");
  assert.deepStrictEqual(await rows(), [`Show only items matching "monitorzzqq" on this page`]);
  await closed();
});

test("the arrows move the choice, stop at each end, and Enter runs the one chosen", { skip }, async () => {
  await page().evaluate(() => go("home"));
  await page().keyboard.press("Control+k");
  await page().keyboard.type("settings");
  const list = await rows();
  assert.ok(list.length >= 2, list.join(" | "));
  assert.strictEqual(await chosen(), list[0]);
  await page().keyboard.press("ArrowUp");
  assert.strictEqual(await chosen(), list[0], "ArrowUp on the first row stays there");
  await page().keyboard.press("ArrowDown");
  assert.strictEqual(await chosen(), list[1]);
  for (let i = 0; i < list.length + 2; i++) await page().keyboard.press("ArrowDown");
  assert.strictEqual(await chosen(), list[list.length - 1], "ArrowDown on the last row stays there");
  for (let i = 0; i < list.length + 2; i++) await page().keyboard.press("ArrowUp");
  assert.strictEqual(list[0], "Settings");
  await page().keyboard.press("Enter");
  assert.strictEqual(await bar().count(), 0, "Enter closes the bar");
  assert.strictEqual(await page().evaluate(() => state.view), "settings");
});

test("Escape closes it and gives focus back to what had it", { skip }, async () => {
  const item = page().locator("#rail-main .rail-item").nth(1);
  await item.focus();
  const label = await item.textContent();
  await page().keyboard.press("Control+k");
  await bar().waitFor();
  await page().keyboard.press("Escape");
  assert.strictEqual(await bar().count(), 0);
  // Focus goes back once the bar has gone, which a busy machine can take a moment to do.
  await page().waitForFunction((label) => document.activeElement?.textContent === label, label, { timeout: 5000 })
    .catch(() => {});
  assert.strictEqual(await page().evaluate(() => document.activeElement?.textContent), label);
});

test("a key typed in the top bar's box opens it with that key, and the last row filters the page", { skip }, async () => {
  await page().evaluate(() => go("home"));
  await page().focus("#search");
  await page().keyboard.type("s");
  await bar().waitFor();
  assert.strictEqual(await page().inputValue("#palette input"), "s");
  await page().keyboard.type("ales");
  await page().locator("#palette .palette-group", { hasText: "Notebooks" }).waitFor();
  assert.ok((await rows()).includes("Sales daily"), "notebook names from the engine are listed");
  const list = await rows();
  for (let i = 0; i < list.length; i++) await page().keyboard.press("ArrowDown");
  assert.strictEqual(await chosen(), `Show only items matching "sales" on this page`);
  await page().keyboard.press("Enter");
  assert.strictEqual(await page().evaluate(() => state.query), "sales");
  assert.strictEqual(await page().inputValue("#search"), "sales");
  await page().fill("#search", "");
  await page().locator("#search").dispatchEvent("input");
  assert.strictEqual(await page().evaluate(() => state.query), "");
});

test("Preview commands are listed only while Preview features show", { skip }, async () => {
  const find = async () => {
    await page().keyboard.press("Control+k");
    await page().keyboard.type("schedules");
    const list = await rows();
    await closed();
    return list;
  };
  const was = await page().evaluate(() => state.settings.show_preview?.value);
  try {
    await page().evaluate(() => { state.settings.show_preview = { ...state.settings.show_preview, value: true }; });
    assert.ok((await find()).includes("KratosForge schedules"));
    await page().evaluate(() => { state.settings.show_preview = { ...state.settings.show_preview, value: false }; });
    assert.ok(!(await find()).includes("KratosForge schedules"));
  } finally {
    await page().evaluate((v) => { state.settings.show_preview = { ...state.settings.show_preview, value: v }; }, was);
  }
});

test("Ctrl+K over an open dialog leaves the dialog alone", { skip }, async () => {
  await page().evaluate(() => showDialog("Something", h("p", {}, "text"), [btn("Close", null, closeDialog, "tool outline")]));
  await page().keyboard.press("Control+k");
  assert.strictEqual(await bar().count(), 0);
  await page().evaluate(() => closeDialog());
});

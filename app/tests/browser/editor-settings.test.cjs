// Settings > Editor (editorprefs.js) against the real engine's settings: word wrap and font size reach open
// notebook cells and SQL editors at once, Vim (Preview) edits modally and Esc still leaves a cell from
// normal mode, and Change keys moves a command's key, refusing reserved keys and another command's.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const save = (changes) => page().evaluate((changes) => saveSettings(changes), changes);
const editing = (on) => page().waitForFunction((on) => !!document.activeElement?.closest(".cm-editor") === on, on, { timeout: 5000 })
  .then(() => on, () => !on);

async function openNotebook() {
  await page().evaluate(() => {
    loadIntoState({ name: "Keys", cells: [{ type: "code", cell_id: "c1", source: "a = 1", meta: {} },
      { type: "code", cell_id: "c2", source: "b = 2", meta: {} }] });
    go("notebook");
  });
  await page().locator(".cell .cm-content").first().waitFor();
}

// A SQL editor on its own, as the SQL page makes one.
const openSql = () => page().evaluate(() => {
  document.getElementById("sql-test")?.remove();
  const box = h("div", { id: "sql-test", style: "width:300px;height:200px" });
  document.body.append(box);
  window.sqlTest = createSqlEditor({ doc: "SELECT a_very_long_column_name, another_long_column_name FROM a_table_with_a_long_name",
    dialect: "spark", provider: { tables: async () => [], columns: async () => [] }, on: { run() {}, save() {}, change() {}, format() { window.formatted = (window.formatted || 0) + 1; } } });
  box.append(window.sqlTest.view.dom);
});
const wraps = (selector) => page().evaluate((selector) => document.querySelector(selector)?.classList.contains("cm-lineWrapping"), selector);

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings", "save_app_settings", "skins_list"],
    mocks: { save_notebook: () => ({ saved: true }), notebook_syntax: () => [] } });
  await page().evaluate(() => loadSettings());
  await save({ show_preview: true });
});
after(async () => { if (app) await app.close(); });

test("the Editor card saves word wrap and font size, and open editors take them at once", { skip }, async () => {
  await openNotebook();
  await openSql();
  assert.strictEqual(await wraps(".cell .cm-content"), false);
  assert.strictEqual(await wraps("#sql-test .cm-content"), false);
  await page().evaluate(() => go("settings"));
  const card = page().locator(".editor-settings");
  await card.getByRole("group", { name: "Word wrap" }).getByRole("button", { name: "SQL" }).click();
  await page().waitForFunction(() => state.settings.editor_word_wrap.value === "sql");
  assert.strictEqual(await wraps("#sql-test .cm-content"), true);
  await card.getByLabel("Font size").selectOption("16");
  await page().waitForFunction(() => state.settings.editor_font_size.value === 16);
  assert.strictEqual(await page().evaluate(() => getComputedStyle(document.querySelector("#sql-test .cm-editor")).fontSize), "16px");
  await openNotebook();
  assert.strictEqual(await wraps(".cell .cm-content"), false, "SQL only: cells don't wrap");
  assert.strictEqual(await page().evaluate(() => getComputedStyle(document.querySelector(".cell .cm-editor")).fontSize), "16px");
  await save({ editor_word_wrap: "both", editor_font_size: null });
  assert.strictEqual(await wraps(".cell .cm-content"), true);
  assert.strictEqual(await page().evaluate(() => getComputedStyle(document.querySelector(".cell .cm-editor")).fontSize), "13px", "back to the cell's own size");
  await save({ editor_word_wrap: null });
  assert.strictEqual(await wraps(".cell .cm-content"), false);
});

test("Vim edits a cell modally, Esc in normal mode still leaves the cell, and it waits for Preview features", { skip }, async () => {
  await save({ editor_keys: "vim" });
  await openNotebook();
  await page().evaluate(() => { selectCell(state.notebook.cells[0]); editors.get(state.notebook.cells[0].id).focus(); });
  assert.strictEqual(await editing(true), true);
  await page().keyboard.press("A");  // append at the end of the line
  await page().keyboard.type("0");
  await page().keyboard.press("Escape");  // insert mode: Vim takes it
  assert.strictEqual(await editing(true), true, "Esc from insert mode stays in the cell");
  await page().keyboard.press("x");  // normal mode: deletes the character under the cursor
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].source), "a = 1");
  await page().keyboard.press("Escape");
  assert.strictEqual(await editing(false), false, "Esc in normal mode leaves for command mode");
  await save({ show_preview: false });
  assert.strictEqual(await page().evaluate(() => !!CM.getCM(editors.get(state.notebook.cells[0].id))), false, "Vim is off with Preview features hidden");
  await save({ show_preview: true, editor_keys: null });
  assert.strictEqual(await page().evaluate(() => !!CM.getCM(editors.get(state.notebook.cells[0].id))), false);
});

test("Change keys moves Format to a new key, refuses a reserved key or another command's, and resets", { skip }, async () => {
  await page().evaluate(() => go("settings"));
  await page().locator(".editor-settings").getByRole("button", { name: "Change keys..." }).click();
  const dialog = page().getByRole("dialog", { name: "Change keys" });
  await dialog.getByPlaceholder("Find a command").fill("format");
  await dialog.getByRole("button", { name: "Change the key for Format" }).click();
  await page().keyboard.press("Control+k");
  await dialog.getByText("Ctrl+K is the command bar, so it can't be used here.").waitFor();
  await page().keyboard.press("Control+/");
  await dialog.getByText('Ctrl+/ is already the key for "Comment or uncomment lines". Change that one first.').waitFor();
  await page().keyboard.press("g");
  await dialog.getByText("On its own, G types or moves the cursor. Add Ctrl or Alt.").waitFor();
  await page().keyboard.press("Alt+Shift+g");
  await page().waitForFunction(() => state.settings.editor_key_overrides.value.format === "Alt-Shift-g");
  await dialog.getByText("Changed").waitFor();
  if (axeSource) {
    // axe covers dialogs: editorprefs.js:editorKeysDialog
    assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  }
  await dialog.getByRole("button", { name: "Done" }).click();
  await page().locator(".editor-settings").getByText("1 changed.").waitFor();

  // An open SQL editor runs Format on the new key, and the old one no longer does.
  await openSql();
  await page().evaluate(() => { window.formatted = 0; window.sqlTest.view.focus(); });
  await page().keyboard.press("Alt+Shift+F");
  await page().keyboard.press("Alt+Shift+G");
  assert.strictEqual(await page().evaluate(() => window.formatted), 1);

  await page().evaluate(() => go("settings"));
  await page().locator(".editor-settings").getByRole("button", { name: "Change keys..." }).click();
  await dialog.getByRole("button", { name: "Reset Format to Alt+Shift+F" }).click();
  await page().waitForFunction(() => !state.settings.editor_key_overrides.value.format);
  await dialog.getByRole("button", { name: "Done" }).click();
});

// Command mode (notebook.js, nbselect.js) reads its keys from the same list: B moves to N, the old key no
// longer adds a cell, a key another command-mode command has (or that starts D D, or that the app keeps)
// is refused before it saves, the Keyboard shortcuts dialog shows the key now, and Reset puts B back.
async function commandMode() {
  await openNotebook();
  await page().waitForFunction(() => {
    const cell = state.notebook.cells[0];
    if (state.selected !== cell.id) selectCell(cell);
    const on = !!document.activeElement?.closest?.(`[data-cell="${cell.id}"]`) && !document.activeElement.closest(".cm-editor");
    if (!on) enterCommandMode(cell);
    return on;
  }, null, { polling: 50 });
}
const cellCount = () => page().evaluate(() => state.notebook.cells.length);
const openKeys = async () => {
  await page().evaluate(() => go("settings"));
  await page().locator(".editor-settings").getByRole("button", { name: "Change keys..." }).click();
  return page().getByRole("dialog", { name: "Change keys" });
};

test("Change keys moves a notebook command-mode key from B to N, refuses clashes, and resets", { skip }, async () => {
  await commandMode();
  await page().keyboard.press("b");
  assert.strictEqual(await cellCount(), 3, "B adds a cell below by default");

  const dialog = await openKeys();
  await dialog.getByPlaceholder("Find a command").fill("insert a cell");
  assert.strictEqual(await dialog.locator("tr[data-command]").count(), 2);
  await dialog.getByText("Notebooks, command mode").first().waitFor();
  const change = () => dialog.getByRole("button", { name: "Change the key for Notebook: insert a cell below (command mode)" }).click();
  await change();
  await page().keyboard.press("a");
  await dialog.getByText('A is already the key for "Notebook: insert a cell above". Change that one first.').waitFor();
  await page().keyboard.press("Enter");
  await dialog.getByText("Enter edits the selected cell, so it can't be used here.").waitFor();
  await page().keyboard.press("d");
  await dialog.getByText('D and DD (the key for "Notebook: delete the cell or the selected cells") start the same way. Change that one first.').waitFor();
  assert.strictEqual(await page().evaluate(() => state.settings.editor_key_overrides.value.nb_insert_below), undefined, "nothing saved");
  await page().keyboard.press("n");
  await page().waitForFunction(() => state.settings.editor_key_overrides.value.nb_insert_below === "n");
  await dialog.getByText("Changed").waitFor();
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  await dialog.getByRole("button", { name: "Done" }).click();

  await commandMode();
  await page().keyboard.press("b");
  assert.strictEqual(await cellCount(), 2, "B no longer adds a cell");
  await page().keyboard.press("n");
  await page().waitForFunction(() => state.notebook.cells.length === 3);
  const listed = await page().evaluate(() => shortcutGroups().flatMap(([, list]) => list)
    .find((s) => s.label === "In command mode: add a code cell above or below").keys);
  assert.deepStrictEqual(listed, ["A", "N"], "the Keyboard shortcuts dialog shows the key now");

  // Two keys one after the other: delete moves from D D to Q Q.
  const keys = await openKeys();
  await keys.getByPlaceholder("Find a command").fill("delete the cell");
  await keys.getByRole("button", { name: "Change the key for Notebook: delete the cell or the selected cells (command mode)" }).click();
  await page().keyboard.press("q");
  await page().keyboard.press("q");
  await page().waitForFunction(() => state.settings.editor_key_overrides.value.nb_delete === "q q");
  await keys.getByRole("button", { name: "Done" }).click();
  await commandMode();
  await page().keyboard.press("d");
  await page().keyboard.press("d");
  assert.strictEqual(await cellCount(), 2, "D D no longer deletes");
  await page().keyboard.press("q");
  await page().keyboard.press("q");
  await page().waitForFunction(() => state.notebook.cells.length === 1);

  const again = await openKeys();
  await again.getByRole("button", { name: "Reset Notebook: insert a cell below (command mode) to B" }).click();
  await page().waitForFunction(() => !state.settings.editor_key_overrides.value?.nb_insert_below);
  await again.getByRole("button", { name: "Reset all" }).click();
  await page().waitForFunction(() => !Object.keys(state.settings.editor_key_overrides.value || {}).length);
  await again.getByRole("button", { name: "Done" }).click();
  await commandMode();
  await page().keyboard.press("b");
  await page().waitForFunction(() => state.notebook.cells.length === 3);
  await commandMode();  // the new cell is being edited
  await page().keyboard.press("d");
  await page().keyboard.press("d");
  await page().waitForFunction(() => state.notebook.cells.length === 1);
});

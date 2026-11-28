// WR-137: the notebook page's keys don't collide across Present (nbpresent.js), the debugger
// (debugging.js), the Markdown toolbar (nbmarkdown.js), find, the command bar and the notebook's own
// command keys.
//   - While presenting, with the debugger paused behind the slides, every key in the Keyboard shortcuts
//     list (shortcutRegistry), and the browser's own F5 and Ctrl+P, does nothing outside Present: no
//     engine call (no step, no run), no dialog, menu or command bar, the cells and the selection as they
//     were, and the window not reloaded.
//   - Ctrl+Shift+F5 opens Present and doesn't Continue; F5 continues and doesn't open Present.
//   - In a Markdown cell with its toolbar, Ctrl+B, Ctrl+I and Ctrl+K only format: no cell added, no command
//     bar. In command mode B adds a cell and Ctrl+B doesn't.
//   - No key is listed twice in one notebook group of the Keyboard shortcuts list.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const ACTS = /^(start_cell|poll_cell|debug_cell|engine_start_cell|engine_poll_cell|run_notebook.*|notebook_vorder_cell|sql_start|restart_kernel|interrupt_kernel|format_cell|format_notebook|notebook_rename|notebook_definition|save_notebook)$/;
const present = () => page().getByRole("dialog", { name: /presenting$/ });

const NB = { name: "Keys", cells: [
  { type: "markdown", cell_id: "m1", source: "# Keys\n\nA slide.", meta: {} },
  { type: "code", cell_id: "c1", source: "x = 1\ny = 2\nz = 3", meta: {} },
  { type: "markdown", cell_id: "m2", source: "## Two\n\nAnother.", meta: {} },
  { type: "code", cell_id: "c2", source: "print(x)", meta: {} },
] };

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["notebook_print_page"], mocks: {
    save_notebook: () => ({ saved: true }), notebook_syntax: () => [], notebook_dependencies: () => ({ cells: [] }),
    debug_cell: () => ({ ok: true }), start_cell: () => ({ id: "run-1" }), poll_cell: () => ({ done: false, outputs: [] }),
  } });
  await page().evaluate((nb) => { window.__nb = nb; window.__loads = 1; }, NB);  // __loads is gone if the window reloads
});
after(async () => { await app?.close(); });

async function load({ paused = false } = {}) {
  await page().evaluate((paused) => {
    closeNotebookPresent();
    document.getElementById("palette")?.remove();
    loadIntoState(structuredClone(window.__nb));
    const c1 = state.notebook.cells[1];
    c1.outputs = [{ type: "stream", text: "one\n" }];
    if (paused) {
      state.settings = { ...(state.settings || {}), show_preview: { value: true } };
      c1.status = "running";
      c1.paused = { line: 2, reason: "breakpoint", stack: [], variables: [], watches: [], evals: [] };
    }
    state.ribbonTab = "view";
    go("notebook");
  }, paused);
  await page().locator(".cell .cm-content").first().waitFor();
}

// Playwright key presses for a key as the shortcuts list writes it, or null for a mouse gesture.
function presses(key) {
  if (/Click$/.test(key)) return null;
  if (key === "DD") return ["d", "d"];
  const map = { Esc: "Escape", Up: "ArrowUp", Down: "ArrowDown", Left: "ArrowLeft", Right: "ArrowRight", Ctrl: "Control", Space: "Space", "-": "Minus", "/": "Slash" };
  const parts = key === "?" ? ["?"] : key.split("+").map((p) => map[p] || (p.length === 1 ? p.toLowerCase() : p));
  return [parts.join("+")];
}

const outside = () => page().evaluate(() => ({
  dialogs: document.getElementById("dialog-root")?.childElementCount || 0,
  menu: !!document.getElementById("menu"),
  palette: !!document.getElementById("palette"),
  find: !!document.querySelector(".nb-find:not([hidden]), .nbfind:not([hidden])"),
  cells: state.notebook.cells.map((c) => `${c.type}:${c.source}`).join("|"),
  selected: state.selected,
  view: state.view,
  loads: window.__loads,
}));

test("no key is listed twice in one notebook group of the Keyboard shortcuts list", { skip }, async () => {
  const dupes = await page().evaluate(() => {
    const seen = new Map();
    const out = [];
    for (const s of shortcutRegistry.filter((x) => x.group.startsWith("Notebook"))) {
      for (const k of s.keys || []) {
        const id = `${s.group}|${k}`;
        if (seen.has(id)) out.push(`${id}: "${seen.get(id)}" and "${s.label}"`);
        else seen.set(id, s.label);
      }
    }
    return out;
  });
  assert.deepStrictEqual(dupes, []);
});

test("while presenting, with the debugger paused, no listed key reaches the notebook, the debugger or the app", { skip }, async () => {
  const keys = await page().evaluate(() => [...new Set(shortcutRegistry
    .filter((s) => /^(Anywhere|Moving around|Notebook)/.test(s.group)).flatMap((s) => s.keys || []))]);
  assert.ok(keys.length > 40, `${keys.length} keys`);
  const own = new Set(["Esc", "Tab", "Shift+Tab", "Left", "Right", "Up", "Down", "PageUp", "PageDown", "Home", "End", "S", "Ctrl+Shift+F5"]);
  const tried = [];
  for (const key of [...keys, "F5", "Ctrl+P", "Ctrl+R", "Alt+Left", "Shift+F10"]) {
    const list = presses(key);
    if (!list || own.has(key)) continue;
    await load({ paused: true });
    await page().locator(".cell").nth(1).locator(".gutter").click();
    await page().keyboard.press("Control+Shift+F5");
    await present().waitFor();
    const was = await outside();
    const calls = app.calls.length;
    for (const k of list) await page().keyboard.press(k);
    await page().waitForTimeout(30);
    const now = await outside();
    // The app's own timers (notices, incidents) poll in the background; these are the calls a key could make.
    const made = app.calls.slice(calls).map((c) => c.method).filter((m) => ACTS.test(m));
    assert.deepStrictEqual(made, [], `${key} while presenting called the engine`);
    assert.deepStrictEqual(now, was, `${key} while presenting changed something behind the slides`);
    assert.strictEqual(await present().count(), 1, `${key} closed Present`);
    tried.push(key);
  }
  // Copy and zoom keep the browser's own behaviour; everything else with Ctrl is stopped.
  const prevented = await page().evaluate(() => ["c", "a", "=", "-", "0", "+", "p", "k"].map((key) => {
    const e = new KeyboardEvent("keydown", { key, ctrlKey: true, bubbles: true, cancelable: true });
    window.dispatchEvent(e);
    return [key, e.defaultPrevented];
  }));
  assert.deepStrictEqual(Object.fromEntries(prevented), { c: false, a: false, "=": false, "-": false, 0: false, "+": false, p: true, k: true });
  assert.ok(tried.includes("F10") && tried.includes("F5") && tried.includes("Ctrl+K") && tried.includes("Shift+Enter") && tried.includes("?"), tried.join(", "));
  await page().keyboard.press("Escape");
});

test("Ctrl+Shift+F5 opens Present and doesn't Continue; F5 continues and doesn't open Present", { skip }, async () => {
  await load({ paused: true });
  await page().locator(".cell").nth(1).locator(".gutter").click();
  let calls = app.calls.length;
  await page().keyboard.press("Control+Shift+F5");
  await present().waitFor();
  assert.deepStrictEqual(app.calls.slice(calls).filter((c) => c.method === "debug_cell"), []);
  await page().keyboard.press("Escape");
  await present().waitFor({ state: "detached" });
  calls = app.calls.length;
  await page().keyboard.press("F5");
  await page().waitForTimeout(50);
  const steps = app.calls.slice(calls).filter((c) => c.method === "debug_cell");
  assert.strictEqual(steps.length, 1);
  assert.strictEqual(steps[0].params.action, "continue");
  assert.strictEqual(await present().count(), 0);
});

test("in a Markdown cell with its toolbar Ctrl+B, Ctrl+I and Ctrl+K only format; in command mode B adds a cell and Ctrl+B doesn't", { skip }, async () => {
  await load({ paused: false });
  await page().evaluate(() => {
    state.settings = { ...(state.settings || {}), show_preview: { value: true } };
    const cell = state.notebook.cells[2];
    cell.editing = true;
    renderNotebook();
    const view = editors.get(cell.id);
    view.focus();
    view.dispatch({ selection: { anchor: 3, head: 6 } });  // "Two"
  });
  await page().locator(".cell").nth(2).getByRole("toolbar", { name: "Markdown formatting" }).waitFor();
  const count = () => page().evaluate(() => state.notebook.cells.length);
  const src = () => page().evaluate(() => state.notebook.cells[2].source);
  await page().keyboard.press("Control+b");
  assert.strictEqual(await src(), "## **Two**\n\nAnother.");
  await page().keyboard.press("Control+i");
  await page().keyboard.press("Control+k");
  assert.strictEqual(await count(), 4);
  assert.strictEqual(await page().locator("#palette").count(), 0, "Ctrl+K added a link, it didn't open the command bar");
  assert.match(await src(), /\]\(url\)/);
  // Command mode: the first cell selected, not editing.
  await page().keyboard.press("Escape");
  await page().locator(".cell").nth(0).locator(".gutter").click();
  await page().keyboard.press("Control+b");
  assert.strictEqual(await count(), 4, "Ctrl+B in command mode adds no cell");
  await page().keyboard.press("b");
  assert.strictEqual(await count(), 5, "B adds a cell below");
});

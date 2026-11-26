// app/ui/features.js beyond the page tests: Learn more links and the shortcuts dialog's notebook guide
// (open_doc with the guide's section), a SQL box's Run text and keys, and the command bar's Notebooks,
// Workspaces and Entities groups, that typed text always offers the page filter, and opening a notebook from it.
// Feature flags are read as they are, never changed. Engine calls are mocked with their own reply shapes.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    open_notebook: ({ name }) => ({ name, cells: [] }),
    // notebook_service.list: the notebooks on this PC, by name.
    list_notebooks: () => [{ name: "Orders clean" }, { name: "Customers" }],
    kernel_variables: () => [], notebook_dependencies: () => ({}),
  } });
  await page().evaluate(() => {
    const real = window.__TAURI__.core.invoke;
    window.__docs = [];
    window.__TAURI__.core.invoke = async (cmd, args) => (cmd === "open_doc" ? (window.__docs.push(args.page), null) : real(cmd, args));
  });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => {
    window.__docs.length = 0;
    document.getElementById("dialog-root").replaceChildren();
    document.getElementById("palette")?.remove();
    go("home");
  });
});

const docs = () => page().evaluate(() => [...window.__docs]);

test("Learn more opens the user guide at its section, or at the top without one", { skip }, async () => {
  await page().evaluate(() => {
    const host = h("div", { id: "kf-links" }, learnMore("sql-limits"), learnMore("", "The guide"));
    host.style.cssText = "position:fixed;top:60px;left:300px;z-index:9999;background:#fff";
    document.body.append(host);
  });
  await page().locator("#kf-links a", { hasText: "Learn more" }).click();
  await page().locator("#kf-links a", { hasText: "The guide" }).click();
  await page().waitForFunction(() => window.__docs.length === 2);
  assert.deepStrictEqual(await docs(), ["user-guide.md#sql-limits", "user-guide.md"]);
  assert.strictEqual(await page().evaluate(() => location.hash), "", "the link doesn't move the page");
  await page().evaluate(() => document.getElementById("kf-links").remove());
  assert.deepStrictEqual(app.errors, []);
});

test("the shortcuts dialog links the notebook guide's editing keys and the user guide's shortcuts", { skip }, async () => {
  await page().evaluate(() => shortcutsDialog());
  const d = page().locator("#dialog-root .dialog");
  await d.getByRole("link", { name: "see the notebook guide" }).click();
  await d.getByRole("link", { name: "Keyboard shortcuts in the user guide" }).click();
  await page().waitForFunction(() => window.__docs.length === 2);
  assert.deepStrictEqual(await docs(), ["notebooks.md#editing", "user-guide.md#keyboard-shortcuts"]);
  assert.ok((await d.locator("h3").allInnerTexts()).includes("Window"), "the zoom keys are listed");
  assert.deepStrictEqual(app.errors, []);
});

test("a SQL box runs the selected text, or all of it when the selection is blank; F5 and Ctrl+Enter are its run keys", { skip }, async () => {
  const got = await page().evaluate(() => {
    const box = document.createElement("textarea");
    box.value = "SELECT 1;\nSELECT 2;";
    document.body.append(box);
    const pick = (from, to) => { box.setSelectionRange(from, to); return boxChosenText(box); };
    const out = [pick(10, 19), pick(9, 10), pick(0, 0)];
    box.remove();
    const key = (key, mods = {}) => isRunKey({ key, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...mods });
    return [out, [key("F5"), key("F5", { shiftKey: true }), key("F5", { ctrlKey: true }), key("Enter"), key("Enter", { ctrlKey: true }), key("Enter", { metaKey: true })]];
  });
  assert.deepStrictEqual(got[0], ["SELECT 2;", "SELECT 1;\nSELECT 2;", "SELECT 1;\nSELECT 2;"], "a selection of only a line break runs it all");
  assert.deepStrictEqual(got[1], [true, false, false, false, true, true]);
  assert.deepStrictEqual(app.errors, []);
});

test("the command bar finds notebooks, workspaces and entities by name, and opens a notebook found by name", { skip }, async () => {
  const groups = await page().evaluate(() => {
    palette.notebooks = [{ name: "Orders clean" }, { name: "Customers" }];
    state.workspaces = [{ id: "w1", displayName: "Orders team" }];
    state.meta = { ...(state.meta || {}), project: { ...(state.meta?.project || {}), entities: [{ name: "orders_silver" }] } };
    return paletteItems("orders").map((it) => [it.group, it.label]).filter(([g]) => g !== "Actions" && g !== "Pages");
  });
  assert.deepStrictEqual(groups, [["Notebooks", "Orders clean"], ["Workspaces", "Orders team"], ["Entities", "orders_silver"],
    ["Filter", 'Show only items matching "orders" on this page']]);

  // Any text offers at least the page filter, so the bar is never empty while something is typed.
  assert.deepStrictEqual(await page().evaluate(() => paletteItems("zzqx no such thing").map((it) => it.group)), ["Filter"]);

  // A notebook found by name opens it (list_notebooks gives the names when the bar opens).
  await page().evaluate(() => openPalette(""));
  await page().getByRole("combobox", { name: "Command bar" }).fill("Orders clean");
  await page().locator("#palette-list").getByRole("option", { name: "Orders clean", exact: true }).click();
  await page().waitForFunction(() => state.view === "notebook" && state.notebook?.name === "Orders clean");
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "open_notebook").map((c) => c.params), [{ name: "Orders clean" }]);
  assert.deepStrictEqual(app.errors, []);
});

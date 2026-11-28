// The item preview panel (item-preview.js) beyond item-preview and item-preview-coverage: the panel's buttons for
// a lakehouse, a table, a notebook and a Fabric item, a text file shown from its start, an empty table with no row
// count, Escape left to an open dialog, an item with nothing to read, and previews hidden with the other preview
// features. The panel is laid out beside an empty page; the preview calls are mocked with the engine's answers
// (preview_service.py, preview.py) and the pages the buttons open are replaced by recorders.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const WS = "11111111-2222-3333-4444-555555555555";
const LH = "aaaaaaaa-0000-0000-0000-000000000003";
const REF = { workspace_id: WS, workspace_name: "Sales Dev", lakehouse_id: LH, name: "LH_Silver" };
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    preview_file: () => ({ kind: "text", text: "id,name\n1,a", truncated: true }),
    preview_table: () => ({ kind: "rows", columns: [{ name: "id", type: "int" }], rows: [], row_count: null }),
    preview_notebook: () => ({ cells: [{ type: "code", source: "x = 1" }], language: "python" }),
  } });
  // The pages the panel's buttons open, recorded instead.
  await page().evaluate(() => {
    window.__went = [];
    for (const name of ["openLakehouse", "openSqlQuery", "openInFabric", "newNotebook", "nbEditFromFabric", "dockFabricView"]) {
      window[`__${name}`] = window[name];
      window[name] = (...a) => { window.__went.push(name); };
    }
  });
});
after(async () => { await app?.close(); });
beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => { window.__went = []; closeDialog(); state.settings = { ...(state.settings || {}), show_preview: { value: true } }; });
  app.errors.length = 0;
});

const show = (target) => page().evaluate((target) => {
  itemPreview.cache.clear();
  document.getElementById("preview-host")?.remove();
  document.body.append(h("div", { id: "preview-host" }, previewLayout(h("div", {}, "page"), null)));
  return selectPreview(target);
}, target);
const panel = () => page().locator("#preview-host .preview-panel");
// What the buttons opened; placing the Fabric view again after a Fabric item is the panel's own business.
const went = (all = false) => page().evaluate((all) => window.__went.filter((n) => all || n !== "dockFabricView"), all);
const click = async (name) => { await panel().getByRole("button", { name: typeof name === "string" ? new RegExp(`^${name}$`) : name }).click(); };

test("a lakehouse's buttons open it, query it and open it in Fabric", { skip }, async () => {
  await show({ kind: "item", item: { workspace_id: WS, workspace_name: "Sales Dev", item_id: LH, type: "Lakehouse", name: "LH_Silver" } });
  await panel().getByText("Click a table or file to preview it.").waitFor();
  for (const b of ["Open", "SQL query", "Open in Fabric"]) await click(b);
  assert.deepStrictEqual(await went(), ["openLakehouse", "openSqlQuery", "openInFabric"]);
  assert.deepStrictEqual(app.errors, []);
});

test("a Fabric item shows Fabric's page and its full page button; scrolling keeps it in place", { skip }, async () => {
  await show({ kind: "item", item: { workspace_id: WS, workspace_name: "Sales Dev", item_id: "r1", type: "Report", name: "Sales report" } });
  await panel().locator(".preview-fabric").waitFor();
  await click("Open full page");
  await page().evaluate(() => {
    const slot = document.querySelector(".preview-fabric");
    fabricView.slot = slot;
    slot.dispatchEvent(new Event("scroll", { bubbles: true }));
  });
  assert.deepStrictEqual(await went(true), ["dockFabricView", "openInFabric"]);
  await page().evaluate(() => { fabricView.slot = null; fabricView.force = false; });
  assert.deepStrictEqual(app.errors, []);
});

test("an empty table with no row count says so; its buttons query it and open a notebook", { skip }, async () => {
  await show({ kind: "table", ref: REF, schema: "dbo", table: "empty" });
  await panel().getByText("The table is empty.").waitFor();
  assert.strictEqual(await panel().locator(".preview-meta").textContent(), "1 columns  ·  read on this PC");
  await click("SQL query");
  await click("Open in notebook");
  assert.deepStrictEqual(await went(), ["openSqlQuery", "newNotebook"]);
  assert.deepStrictEqual(app.errors, []);
});

test("a text file shows its start; a notebook's buttons edit it here and open it in Fabric", { skip }, async () => {
  await show({ kind: "file", ref: REF, path: "Files/raw/a.csv", size: 2048 });
  await panel().getByText("Showing the start of the file.").waitFor();
  assert.strictEqual(await panel().locator("pre.preview-text").textContent(), "id,name\n1,a");
  await show({ kind: "item", item: { workspace_id: WS, workspace_name: "Sales Dev", item_id: "nb1", type: "Notebook", name: "nb_load" } });
  await panel().locator(".preview-cell").waitFor();
  await click(/^Edit on this PC/);
  await click("Open in Fabric");
  assert.deepStrictEqual(await went(), ["nbEditFromFabric", "openInFabric"]);
  assert.deepStrictEqual(app.errors, []);
});

test("Escape is the dialog's while one is open; an item with nothing to read loads nothing; hidden previews don't open", { skip }, async () => {
  await show({ kind: "table", ref: REF, schema: "dbo", table: "empty" });
  await panel().getByText("The table is empty.").waitFor();
  await page().evaluate(() => showDialog("Something else", document.createElement("div"), [btn("Close", null, closeDialog, "tool")]));
  await page().keyboard.press("Escape");
  assert.strictEqual(await page().evaluate(() => !!itemPreview.target), true, "the panel stays while the dialog has Escape");
  await page().evaluate(() => closeDialog());
  assert.strictEqual(await page().evaluate(() => loadPreview({ kind: "item", item: { item_id: "x", type: "Report" } })), null);
  const opened = await page().evaluate(() => {
    const was = PREVIEW_FEATURES.itemPreview;
    PREVIEW_FEATURES.itemPreview = true;
    state.settings.show_preview = { value: false };
    try { return selectPreview({ kind: "table", ref: {}, schema: "a", table: "b" }); } finally { PREVIEW_FEATURES.itemPreview = was; state.settings.show_preview = { value: true }; }
  });
  assert.strictEqual(opened, false);
  await page().evaluate(() => { closePreview(); document.getElementById("preview-host")?.remove(); });
  assert.deepStrictEqual(app.errors, []);
});

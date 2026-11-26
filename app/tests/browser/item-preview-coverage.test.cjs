// The item preview panel (item-preview.js) beyond item-preview.test.cjs: a file too big to download for
// a preview, and a notebook with no cells. The panel is laid out beside an empty page and the preview
// calls are mocked with the engine's answers (preview_service.py, preview.py).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const WS = "11111111-2222-3333-4444-555555555555";
const LH = "aaaaaaaa-0000-0000-0000-000000000001";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    preview_file: ({ size }) => ({ kind: "too_big", size }),
    preview_notebook: () => ({ cells: [], language: "" }),
  } });
});
after(async () => { await app?.close(); });

// Lays the panel out beside an empty page and previews target, in one step: a page the app draws
// meanwhile (Workspaces has a panel of its own) can't take the panel over in between.
const show = (target) => page().evaluate((target) => {
  itemPreview.cache.clear();
  document.getElementById("preview-host")?.remove();
  document.body.append(h("div", { id: "preview-host" }, previewLayout(h("div", {}, "page"), null)));
  selectPreview(target);
}, target);

const panel = () => page().locator("#preview-host .preview-panel");

test("a file too big to download says so instead of a preview", { skip }, async () => {
  await show({ kind: "file", path: "Files/raw/events.parquet", size: 3 * 1024 ** 3,
    ref: { workspace_id: WS, workspace_name: "Sales Dev", lakehouse_id: LH, name: "LH_Silver" } });
  await panel().getByText(/too big to download for a preview\.$/).waitFor();
  assert.strictEqual(await panel().locator(".preview-title .name").textContent(), "events.parquet");
  assert.match(await panel().locator(".preview-body").textContent(), /^This file is 3(\.0)? GB, too big/);
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "preview_file").at(-1).params,
    { lakehouse_ref: { workspace_id: WS, workspace_name: "Sales Dev", lakehouse_id: LH, name: "LH_Silver" }, path: "Files/raw/events.parquet", size: 3 * 1024 ** 3 });
});

test("a notebook with no cells says so and counts none", { skip }, async () => {
  await show({ kind: "item", item: { workspace_id: WS, workspace_name: "Sales Dev", item_id: "nb-empty", type: "Notebook", name: "nb_empty" } });
  await panel().getByText("This notebook has no cells.").waitFor();
  assert.strictEqual(await panel().locator(".preview-meta").textContent(), "0 cells  ·  0 code");
  assert.match(await panel().locator(".preview-body").textContent(), /Read only\. Outputs aren't shown; Fabric keeps them with each run\./);
  await panel().getByRole("button", { name: "Close preview" }).click();
  assert.strictEqual(await panel().evaluate((p) => p.classList.contains("open")), false);
});

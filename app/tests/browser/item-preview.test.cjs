// #1396: the item preview side panel on the Workspaces page (item-preview.js, see harness.cjs for how to
// run it). Clicking a table, file or notebook previews it through preview_table, preview_file or
// preview_notebook with exactly that item's reference (lakehouse, schema, table, path), and the panel
// shows what the engine returned: rows and columns, a file's text or a note for a file it can't show, a
// notebook's cells. Any other item shows Fabric's own page in the panel and nothing is read on this PC.
// Opening another item replaces the panel, and an answer for an item no longer shown is ignored (a held,
// deferred answer let go late). Close and Escape close the panel and return focus to the row that
// opened it. An engine error shows a plain message. Nothing writes, and axe finds nothing serious on
// the panel for each kind.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

const WS = "11111111-2222-3333-4444-555555555555";
const SILVER = "aaaaaaaa-0000-0000-0000-000000000001";
const GOLD = "aaaaaaaa-0000-0000-0000-000000000002";
const NB = "bbbbbbbb-0000-0000-0000-000000000001";
const NB_SLOW = "bbbbbbbb-0000-0000-0000-000000000002";
const REPORT = "cccccccc-0000-0000-0000-000000000001";

// What the preview panel reads, and every engine call that would change something from this page.
const READS = ["list_workspaces", "workspace_marks", "list_items", "list_tables", "list_files",
  "preview_table", "preview_file", "preview_notebook"];
const WRITES = ["set_workspace_mark", "create_lakehouse", "new_notebook", "notebook_from_fabric", "notebook_fabric_check"];

// The engine's answers, shaped as it sends them: list_tables {schema: [names]} (service.py), list_files
// {name, path, is_dir, size, modified} (onelake.py FileStore.list), preview_table {columns, rows,
// row_count, version} (preview.py table_preview), preview_file {kind: "table"|"text"|"other"|"too_big"}
// (preview_service.py, preview.py file_preview) and preview_notebook {cells: [{type, source}], language}
// (preview.py notebook_cells). Each answer names the lakehouse and table it is for, so a panel that
// shows another item's answer is seen.
const LAKEHOUSES = { [SILVER]: "LH_Silver", [GOLD]: "LH_Gold" };
const held = {};  // item id -> resolve, for an answer the test lets go of later
function heldAnswer(id, answer) {
  return new Promise((resolve) => { held[id] = () => resolve(answer); });
}
const mocks = {
  readiness_check: () => [],
  list_workspaces: () => [{ id: WS, displayName: "Sales Dev" }],
  workspace_marks: () => ({ favorites: [], pins: [] }),
  list_items: () => [
    { id: SILVER, displayName: "LH_Silver", type: "Lakehouse", workspaceId: WS },
    { id: GOLD, displayName: "LH_Gold", type: "Lakehouse", workspaceId: WS },
    { id: NB, displayName: "nb_orders", type: "Notebook", workspaceId: WS },
    { id: NB_SLOW, displayName: "nb_slow", type: "Notebook", workspaceId: WS },
    { id: REPORT, displayName: "Sales report", type: "Report", workspaceId: WS },
  ],
  list_tables: () => ({ dbo: ["broken", "orders"], sales: ["orders"] }),
  list_files: ({ lakehouse_id, directory }) => directory !== "Files" ? [] : [
    { name: "raw", path: "Files/raw", is_dir: true, size: 0, modified: "" },
    { name: "notes.txt", path: "Files/notes.txt", is_dir: false, size: 42, modified: "2026-10-30T08:00:00Z" },
    { name: "logo.png", path: "Files/logo.png", is_dir: false, size: 2048, modified: "2026-10-30T08:00:00Z" },
    { name: `${LAKEHOUSES[lakehouse_id]}.csv`, path: `Files/${LAKEHOUSES[lakehouse_id]}.csv`, is_dir: false, size: 99, modified: "" },
  ],
  preview_table: ({ lakehouse_ref, table }) => {
    if (table === "dbo.broken") throw new Error("Could not open the table dbo.broken: 403 Forbidden from onelake.dfs.fabric.microsoft.com");
    const lh = LAKEHOUSES[lakehouse_ref.lakehouse_id];
    return { columns: [{ name: "order_id", type: "long" }, { name: "source", type: "string" }, { name: "note", type: "string" }],
      rows: [[1, `${lh} ${table}`, null], [2, `${lh} ${table}`, "late"]], row_count: 1234, version: 7 };
  },
  preview_file: ({ lakehouse_ref, path }) => {
    if (path.endsWith(".png")) return { kind: "other" };
    if (path.endsWith(".txt")) return { kind: "text", text: `Read me from ${LAKEHOUSES[lakehouse_ref.lakehouse_id]}`, truncated: false };
    return { kind: "table", columns: [{ name: "region", type: "string" }], rows: [[`${path} in ${LAKEHOUSES[lakehouse_ref.lakehouse_id]}`]], row_count: null };
  },
  preview_notebook: ({ item_id }) => {
    if (item_id !== NB) return heldAnswer(item_id, { cells: [{ type: "markdown", source: "# Slow notebook" }], language: "scala" });
    return { cells: [{ type: "markdown", source: "# Orders notebook" }, { type: "code", source: 'df = spark.read.table("orders")' },
      { type: "code", source: "display(df)" }], language: "python" };
  },
};

let baseline;  // the methods the app calls on its own, before the page opens
let first;     // where this file's calls start

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await startupReads(app);
  baseline = new Set(app.calls.map((c) => c.method));
  first = app.calls.length;
  await page().evaluate(() => { state.user = "chris@contoso.com"; state.workspaces = null; go("workspaces"); });
  await page().getByRole("button", { name: "Show items" }).click();
  await page().locator(".ws-items tr", { hasText: "Sales report" }).waitFor();
});
after(async () => { if (app) await app.close(); });

const panel = () => page().locator(".preview-panel.open");
const row = (name) => page().locator(".ws-items tr", { hasText: name });
const since = (n, method) => app.calls.slice(n).filter((c) => c.method === method).map((c) => c.params);
const shellCalls = () => page().evaluate(() => window.__fdShellCalls.map((c) => ({ cmd: c.cmd, page: c.args?.page })));

// Opens a lakehouse in the panel and its Tables and Files folders, ready for a table or file click.
async function openLakehouse(name) {
  if (await panel().count()) await panel().getByRole("button", { name: "Close preview" }).click();
  await row(name).click();
  await panel().locator(".preview-title .name", { hasText: name }).waitFor();
  // The tree keeps what was open for each lakehouse, so a folder already open stays as it is.
  if (!(await panel().locator(".tree-row", { hasText: "sales" }).count())) await panel().locator(".tree-row", { hasText: "Tables" }).click();
  await panel().locator(".tree-row", { hasText: "sales" }).waitFor();
  if (!(await panel().locator(".tree-row", { hasText: "notes.txt" }).count())) await panel().locator(".tree-row", { hasText: /^Files/ }).click();
  await panel().locator(".tree-row", { hasText: "notes.txt" }).waitFor();
}

// The table row under a schema in the panel's tree: the tree lists dbo's tables, then sales's.
const tableRow = (schema, table) => panel().locator(`.tree-row[title="Preview ${schema}.${table}"]`);
const bodyText = () => panel().locator(".preview-body").textContent();

test("a table sends its own lakehouse, schema and table and shows the rows and columns returned", { skip }, async () => {
  await openLakehouse("LH_Silver");
  await openLakehouse("LH_Gold");
  const n = app.calls.length;
  await tableRow("sales", "orders").click();
  await panel().locator(".preview-grid tbody tr").first().waitFor();
  assert.deepStrictEqual(since(n, "preview_table"), [{
    lakehouse_ref: { workspace_id: WS, workspace_name: "Sales Dev", lakehouse_id: GOLD, name: "LH_Gold" }, table: "sales.orders" }]);
  assert.strictEqual(await panel().locator(".preview-title .name").textContent(), "sales.orders");
  assert.deepStrictEqual(await panel().locator(".preview-grid thead th").allTextContents(), ["order_idlong", "sourcestring", "notestring"]);
  assert.deepStrictEqual(await panel().locator(".preview-grid tbody tr").evaluateAll((trs) => trs.map((tr) => [...tr.cells].map((c) => c.textContent))),
    [["1", "LH_Gold sales.orders", "null"], ["2", "LH_Gold sales.orders", "late"]]);
  assert.match(await panel().locator(".preview-meta").textContent(), /^1,234 rows {2}· {2}3 columns {2}· {2}read on this PC$/);
  assert.match(await bodyText(), /Showing the first 2 rows\./);
  // Schema lists each column with its type.
  await panel().getByRole("tab", { name: "Schema" }).click();
  assert.deepStrictEqual(await panel().locator(".preview-grid tbody tr").allTextContents(), ["order_idlong", "sourcestring", "notestring"]);
  assert.deepStrictEqual(axeSource ? await axeFindings(page(), ".preview-panel.open") : [], []);
  // Back returns to the lakehouse it was picked from.
  await panel().getByRole("button", { name: "Back" }).click();
  await panel().locator(".preview-title .name", { hasText: "LH_Gold" }).waitFor();
  assert.deepStrictEqual(since(n, "preview_table").length, 1, "Back reads nothing again");
  // The same table in another lakehouse is its own item: read with that lakehouse, not shown from the first.
  await openLakehouse("LH_Silver");
  const m = app.calls.length;
  await tableRow("sales", "orders").click();
  await panel().locator(".preview-grid tbody td", { hasText: "LH_Silver sales.orders" }).first().waitFor();
  assert.deepStrictEqual(since(m, "preview_table").map((p) => [p.lakehouse_ref.lakehouse_id, p.table]), [[SILVER, "sales.orders"]]);
  assert.doesNotMatch(await bodyText(), /LH_Gold/);
});

test("a file sends its own lakehouse and path and shows its text, rows, or a note it can't be shown", { skip }, async () => {
  await openLakehouse("LH_Silver");
  let n = app.calls.length;
  await panel().locator(".tree-row", { hasText: "notes.txt" }).click();
  await panel().locator("pre.preview-text").waitFor();
  const silver = { workspace_id: WS, workspace_name: "Sales Dev", lakehouse_id: SILVER, name: "LH_Silver" };
  assert.deepStrictEqual(since(n, "preview_file"), [{ lakehouse_ref: silver, path: "Files/notes.txt", size: 42 }]);
  assert.strictEqual(await panel().locator("pre.preview-text").textContent(), "Read me from LH_Silver");
  assert.strictEqual(await panel().locator(".preview-title .name").textContent(), "notes.txt");
  assert.deepStrictEqual(axeSource ? await axeFindings(page(), ".preview-panel.open") : [], []);

  await panel().getByRole("button", { name: "Back" }).click();
  n = app.calls.length;
  await panel().locator(".tree-row", { hasText: "LH_Silver.csv" }).click();
  await panel().locator(".preview-grid tbody td", { hasText: "Files/LH_Silver.csv in LH_Silver" }).waitFor();
  assert.deepStrictEqual(since(n, "preview_file"), [{ lakehouse_ref: silver, path: "Files/LH_Silver.csv", size: 99 }]);
  assert.match(await panel().locator(".preview-meta").textContent(), /^1 columns/, "no row count when the engine has none");

  await panel().getByRole("button", { name: "Back" }).click();
  n = app.calls.length;
  await panel().locator(".tree-row", { hasText: "logo.png" }).click();
  await panel().locator(".preview-body", { hasText: "can't be previewed" }).waitFor();
  assert.deepStrictEqual(since(n, "preview_file"), [{ lakehouse_ref: silver, path: "Files/logo.png", size: 2048 }]);
  assert.match(await bodyText(), /^This kind of file can't be previewed\. Data files \(CSV, Parquet, JSON\) and text files can\.$/);
  assert.deepStrictEqual(axeSource ? await axeFindings(page(), ".preview-panel.open") : [], []);
});

test("a notebook sends its own workspace and item and shows its cells", { skip }, async () => {
  const n = app.calls.length;
  await row("nb_orders").click();
  await panel().locator(".preview-cell").first().waitFor();
  assert.deepStrictEqual(since(n, "preview_notebook"), [{ workspace_id: WS, item_id: NB }]);
  assert.strictEqual(await panel().locator(".preview-title .name").textContent(), "nb_orders");
  assert.strictEqual(await panel().locator(".preview-md h1").textContent(), "Orders notebook");
  assert.deepStrictEqual(await panel().locator(".preview-cell").allTextContents(), ['[1]df = spark.read.table("orders")', "[2]display(df)"]);
  assert.match(await panel().locator(".preview-meta").textContent(), /^3 cells {2}· {2}2 code {2}· {2}python$/);
  assert.match(await row("nb_orders").getAttribute("class"), /selected/, "the row shows it is the one previewed");
  assert.deepStrictEqual(axeSource ? await axeFindings(page(), ".preview-panel.open") : [], []);
});

test("another item type shows Fabric's page in the panel and reads nothing on this PC", { skip }, async () => {
  const n = app.calls.length;
  await page().evaluate(() => { window.__fdShellCalls.length = 0; });
  await row("Sales report").click();
  await panel().locator(".preview-fabric").waitFor();
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "fabric_view_show"));
  const shown = (await shellCalls()).filter((c) => c.cmd === "fabric_view_show");
  assert.deepStrictEqual(shown.map((c) => c.page), [`https://app.fabric.microsoft.com/groups/${WS}/reports/${REPORT}?experience=fabric-developer`]);
  assert.deepStrictEqual(app.calls.slice(n).map((c) => c.method).filter((m) => m.startsWith("preview_")), []);
  assert.match(await panel().locator(".preview-head").textContent(), /Sales report/);
  assert.deepStrictEqual(axeSource ? await axeFindings(page(), ".preview-panel.open") : [], []);
  // A notebook after it takes Fabric's page away again.
  await page().evaluate(() => { window.__fdShellCalls.length = 0; });
  await row("nb_orders").click();
  await panel().locator(".preview-cell").first().waitFor();
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "fabric_view_hide"));
});

// Once the page has the item's answer (it keeps one it read in its cache) and has drawn after it.
async function answered(id) {
  await page().waitForFunction((id) => itemPreview.cache.has(`i:${id}`), id);
  await page().evaluate(() => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 50))));
}

test("opening another item replaces the panel, and a late answer for an item no longer shown is ignored", { skip }, async () => {
  await page().evaluate(() => itemPreview.cache.clear());  // both read now, not from the earlier test
  const n = app.calls.length;
  await row("nb_slow").click();
  await panel().locator(".preview-title .name", { hasText: "nb_slow" }).waitFor();
  await page().waitForFunction(() => /Loading the preview/.test(document.querySelector(".preview-panel.open .preview-body")?.textContent));
  assert.ok(held[NB_SLOW], "nb_slow's answer is held");
  // Another item replaces it while nb_slow is still reading.
  await row("nb_orders").click();
  await panel().locator(".preview-md h1", { hasText: "Orders notebook" }).waitFor();
  assert.strictEqual(await page().locator(".preview-panel").count(), 1, "one panel, replaced");
  held[NB_SLOW]();
  await answered(NB_SLOW);
  assert.strictEqual(await panel().locator(".preview-title .name").textContent(), "nb_orders");
  assert.strictEqual(await panel().locator(".preview-md h1").textContent(), "Orders notebook");
  assert.doesNotMatch(await panel().textContent(), /Slow notebook|scala/);
  assert.match(await panel().locator(".preview-meta").textContent(), /^3 cells {2}· {2}2 code {2}· {2}python$/);
  assert.deepStrictEqual(since(n, "preview_notebook").map((p) => p.item_id), [NB_SLOW, NB]);

  // A late answer after the panel was closed doesn't open it again.
  delete held[NB_SLOW];
  await page().evaluate(() => itemPreview.cache.clear());
  await row("nb_slow").click();
  await page().waitForFunction(() => /Loading the preview/.test(document.querySelector(".preview-panel.open .preview-body")?.textContent));
  for (const end = Date.now() + 5000; !held[NB_SLOW]; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("nb_slow was never read");
  }
  await panel().getByRole("button", { name: "Close preview" }).click();
  held[NB_SLOW]();
  await answered(NB_SLOW);
  assert.strictEqual(await panel().count(), 0, "the panel stays closed");
  assert.strictEqual(await page().locator(".preview-panel").textContent(), "");
});

// The row focus is on, as its text, and whether it is a Workspaces item row.
const focused = () => page().evaluate(() => {
  const el = document.activeElement;
  return { text: el?.closest?.("tr")?.textContent || el?.tagName, itemRow: !!el?.matches?.(".ws-items tr") };
});

test("Close and Escape close the panel and return focus to the item that opened it", { skip }, async () => {
  // Close, from a mouse click on the row.
  await row("nb_orders").click();
  await panel().locator(".preview-cell").first().waitFor();
  await panel().getByRole("button", { name: "Close preview" }).click();
  await page().waitForFunction(() => !document.querySelector(".preview-panel.open"));
  let at = await focused();
  assert.ok(at.itemRow && at.text.startsWith("nb_orders"), `focus is back on nb_orders, not ${at.text}`);

  // Escape, from the keyboard: Enter opens the row that has focus.
  await row("Sales report").focus();
  await page().keyboard.press("Enter");
  await panel().locator(".preview-fabric").waitFor();
  await panel().getByRole("button", { name: "Close preview" }).focus();
  await page().keyboard.press("Escape");
  await page().waitForFunction(() => !document.querySelector(".preview-panel.open"));
  at = await focused();
  assert.ok(at.itemRow && at.text.startsWith("Sales report"), `focus is back on Sales report, not ${at.text}`);
  await page().waitForFunction(() => window.__fdShellCalls.at(-1)?.cmd === "fabric_view_hide");

  // A table picked inside a lakehouse's preview: focus goes back to the lakehouse's row.
  await openLakehouse("LH_Silver");
  await tableRow("dbo", "orders").click();
  await panel().locator(".preview-grid tbody tr").first().waitFor();
  await page().keyboard.press("Escape");
  await page().waitForFunction(() => !document.querySelector(".preview-panel.open"));
  at = await focused();
  assert.ok(at.itemRow && at.text.startsWith("LH_Silver"), `focus is back on LH_Silver, not ${at.text}`);

  // Escape with nothing previewed changes nothing.
  await page().keyboard.press("Escape");
  assert.strictEqual(await panel().count(), 0);
});

test("an engine error shows a plain message with Try again", { skip }, async () => {
  await openLakehouse("LH_Silver");
  const n = app.calls.length;
  await tableRow("dbo", "broken").click();
  const error = panel().locator(".preview-body .error-text");
  await error.waitFor();
  assert.deepStrictEqual(since(n, "preview_table").map((p) => p.table), ["dbo.broken"]);
  const text = await error.textContent();
  assert.match(text, /^Could not preview it: \S/);
  assert.doesNotMatch(text, /\[object Object\]|undefined|Traceback/);
  await panel().getByRole("button", { name: "Try again" }).click();
  for (const end = Date.now() + 5000; since(n, "preview_table").length < 2; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("Try again read nothing");
  }
  await error.waitFor();
  assert.deepStrictEqual(axeSource ? await axeFindings(page(), ".preview-panel.open") : [], []);
});

test("the preview is read only: nothing writes", { skip }, async () => {
  const made = app.calls.slice(first).map((c) => c.method);
  for (const m of READS) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
  assert.deepStrictEqual([...new Set(made.filter((m) => !READS.includes(m) && !baseline.has(m)))], []);
  assert.deepStrictEqual(app.errors, []);
});

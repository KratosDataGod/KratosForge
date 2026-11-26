// OneLake > Shortcuts (shortcuts.js) beyond shortcuts-coverage and the other shortcut tests: the tiles that open
// Health, Access, Changes and Requests, more than 500 shortcuts, broken shortcuts whose lakehouse is gone (found
// with every table, not found, and a search that failed), Health's Open and Repoint, Access with a gap and with
// none, Who can read for a storage shortcut, the change helpers, and New shortcut's storage with no connection,
// a folder under Files, a rules warning and rules that couldn't be checked. The engine is mocked (see harness.cjs).
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ME = "ana@contoso.com";
const at = (s) => new Date(Date.now() + s * 1000).toISOString();
const lakehouse = (ws, wsName, id, name) => ({ workspace_id: ws, workspace_name: wsName, item_id: id, lakehouse_id: id, name, type: "Lakehouse", description: "" });
const CATALOG = {
  workspaces: [{ id: "ws-dev", displayName: "Sales Dev" }, { id: "ws-old", displayName: "Sales Old" }],
  items: [lakehouse("ws-dev", "Sales Dev", "lh-bronze", "LH_Bronze"), lakehouse("ws-dev", "Sales Dev", "lh-silver", "LH_Silver")],
};
const WS = { "lh-bronze": ["ws-dev", "Sales Dev", "LH_Bronze"], "lh-silver": ["ws-dev", "Sales Dev", "LH_Silver"] };
function row(lh, path, name, more = {}) {
  const [ws, wsName, lhName] = WS[lh];
  return { id: `${lh}|${path}/${name}`, name, path, full_path: `${path}/${name}`, kind: path.startsWith("Tables") ? "table" : "folder",
    target_type: "OneLake", target: "Tables/dbo/orders", workspace_id: "ws-dev", item_id: "lh-bronze", target_path: "Tables/dbo/orders",
    exposure: "identity", lakehouse_id: lh, host_workspace_id: ws, workspace_name: wsName, lakehouse_name: lhName,
    health: "healthy", health_detail: "", checked_at: at(-3600), first_seen: at(-86400), host_env: "dev", target_env: "dev",
    product: null, risks: [], same_env_target: null, used_by: [], last_change: null, ...more };
}
const ADLS = { target_type: "ADLS Gen2", target: "https://raw.dfs.core.windows.net/crm", location: "https://raw.dfs.core.windows.net", subpath: "/crm",
  connection_id: "conn-adls", exposure: "connection", item_id: null, workspace_id: null, target_path: null, target_env: "" };
// A broken shortcut into a lakehouse no workspace the person can open has.
const gone = (name, item, lhName) => row("lh-silver", "Tables/dbo", name, { health: "broken", health_detail: "The target isn't there", workspace_id: "ws-old",
  item_id: item, target_lakehouse_name: lhName, target_workspace_name: "Sales Old" });

let db;
let app;
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);
const copy = (x) => JSON.parse(JSON.stringify(x));
const counts = (rows) => ({ total: rows.length, lakehouses: 2, workspaces: 1, broken: rows.filter((s) => s.health === "broken").length,
  unreachable: rows.filter((s) => s.health === "unreachable").length, not_checked: 0, connection: 1, risky: 0, changed_7d: 0, waiting: 0 });

before(async () => {
  if (skip) return;
  app = await openApp({
    storage: { "fd.shortcuts.checkOnOpen": "0" },
    mocks: {
      readiness_check: () => [],
      catalog: () => CATALOG,
      list_tables: ({ lakehouse_id }) => ({ "lh-bronze": { dbo: ["orders"] }, "lh-silver": { dbo: ["orders"] } }[lakehouse_id] || {}),
      shortcut_center: () => ({ pending: false, rows: copy(db.rows), counts: counts(db.rows), errors: {}, problems: [], needs: [], requests: [], checked_at: at(-3600), admin: false }),
      shortcut_readers: () => ({ mode: "connection", text: "Everyone who can read LH_Bronze reads the storage through the connection's account.",
        readers: [{ id: "u1", name: "Ana", email: ME, how: "Workspace Member" }], missing: [], target: null, gap: 0, partial: false }),
      shortcut_changes: () => ({ changes: [] }),
      shortcut_access: () => ({ pending: false, items: db.access }),
      // shortcuts.py rules(): the rules, their wording, and whether this person may change them.
      shortcut_rules: () => ({ rules: { cross_env: "block", storage_role: "warn", deprecated_product: "warn", connections: "off", allowed_connections: [], who_creates: "anyone" },
        text: { cross_env: "A shortcut may not point at another environment", storage_role: "A storage shortcut should have a data access role on its folder",
          deprecated_product: "A shortcut should not point at a deprecated data product", connections: "Storage shortcuts use only the allowed connections",
          who_creates: "Who may create shortcuts from KratosForge" }, admin: false, problems: [] }),
      shortcut_preflight: () => db.preflight(),
      shortcut_connections: () => [{ id: "conn-adls", name: "Raw storage", type: "adlsGen2", path: "https://raw.dfs.core.windows.net" }],
      // shortcuts.py find_moved: by the lakehouse the shortcuts pointed at.
      shortcut_find_moved: ({ shortcuts }) => {
        const item = shortcuts[0].item_id;
        if (item === "lh-err") throw new Error("The catalog couldn't be read");
        if (item === "lh-moved") return { candidate: { lakehouse_name: "LH_Moved", workspace_name: "Sales Dev", workspace_id: "ws-dev", lakehouse_id: "lh-bronze" }, found: shortcuts.map((s) => s.id) };
        return { candidate: null, found: [] };
      },
      create_shortcut: () => [{ name: "crm" }],
    },
  });
});

after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  db = { rows: [row("lh-silver", "Tables/dbo", "orders"), row("lh-bronze", "Files", "landing", { ...ADLS })], access: {}, preflight: () => ({ blocked: [], warnings: [] }) };
  await page().evaluate(() => { closeDialog(); toastClose(); document.getElementById("toast").textContent = ""; go("home"); });
  app.calls.length = 0;
  app.errors.length = 0;
});

async function open(view = "all", ready = ".sc-body .sc-pad") {
  await page().evaluate(([v, me]) => {
    state.user = me; catalogPage.tab = "shortcuts";
    Object.assign(scPage, { view: v, data: null, selected: null, access: null, changes: null, moved: null, query: "", ws: "all", type: "all", health: "all", panelTab: "overview" });
    go("onelake");
  }, [view, ME]);
  await page().waitForSelector(ready);
}
const toastIs = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);
const panel = () => page().locator(".preview-panel.open");
const view = () => page().evaluate(() => scPage.view);

test("the tiles open Health, Access, Changes and Requests", { skip }, async () => {
  for (const [tile, want] of [["Broken", "health"], ["Read with a connection", "access"], ["Changed in 7 days", "changes"], ["Waiting on you", "requests"]]) {
    await open();
    await page().locator(".plat-stat", { hasText: tile }).click();
    assert.strictEqual(await view(), want);
  }
  assert.deepStrictEqual(app.errors, []);
});

test("more than 500 shortcuts show the first 500 and say how to narrow them", { skip }, async () => {
  db.rows = Array.from({ length: 501 }, (_, i) => row("lh-silver", "Tables/dbo", `t${i}`));
  await open();
  await page().getByText("Showing 500 of 501. Search or filter to narrow it.").waitFor();
  assert.strictEqual(await page().locator(".sc-table tbody tr").count(), 500);
  assert.deepStrictEqual(app.errors, []);
});

test("broken shortcuts whose lakehouse is gone say where it went, that it wasn't found, or that the search failed", { skip }, async () => {
  db.rows = [gone("a", "lh-moved", "LH_Moved"), gone("b", "lh-moved", "LH_Moved"), gone("c", "lh-lost", "LH_Lost"), gone("d", "lh-err", "LH_Err"),
    row("lh-silver", "Tables/dbo", "far", { health: "unreachable", health_detail: "403 from the target" })];
  await open("health");
  await page().getByText("A lakehouse called LH_Moved is now in Sales Dev, and all 2 tables are there.").waitFor();
  await page().getByText("No lakehouse called LH_Lost in a workspace you can open.").waitFor();
  await page().getByText("Couldn't look for where it went: The catalog couldn't be read").waitFor();
  assert.match(await page().locator(".sc-body").innerText(), /LH_Lost in Sales Old isn't in a workspace you can open/);
  assert.strictEqual(calls("shortcut_find_moved").length, 3);

  // An unreachable shortcut has Open, which shows it in the list with its panel.
  const far = page().locator(".sc-item", { hasText: "far" });
  await far.getByRole("button", { name: "Open" }).click();
  await panel().waitFor();
  assert.deepStrictEqual(await page().evaluate(() => [scPage.view, scPage.selected, scPage.panelTab]), ["all", "lh-silver|Tables/dbo/far", "overview"]);
  assert.deepStrictEqual(app.errors, []);
});

test("Health's Repoint opens the Repoint dialog for a broken OneLake shortcut", { skip }, async () => {
  db.rows = [row("lh-silver", "Tables/dbo", "orders", { health: "broken", health_detail: "The target table isn't there any more" })];
  await open("health");
  await page().locator(".sc-item", { hasText: "orders" }).getByRole("button", { name: "Repoint" }).click();
  await page().getByRole("dialog", { name: "Repoint orders" }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Access lists a shortcut whose readers and target don't match with Open, and says when every one matches", { skip }, async () => {
  db.access = { "lh-silver|Tables/dbo/orders": { mode: "identity", here: 5, target: 3, gap: 2, missing: [{ id: "u2" }], text: "2 readers here can't read the target" } };
  await open("access");
  const r = page().locator("table.list tbody tr", { hasText: "orders" });
  await r.waitFor();
  assert.match(await r.innerText(), /5\s+3\s+2 readers here can't read the target/);
  await r.getByRole("button", { name: "Open" }).click();
  await panel().waitFor();
  assert.deepStrictEqual(await page().evaluate(() => [scPage.view, scPage.panelTab]), ["all", "readers"]);

  db.rows = [row("lh-silver", "Tables/dbo", "orders")];
  db.access = { "lh-silver|Tables/dbo/orders": { mode: "identity", here: 5, target: 5, gap: 0, missing: [], text: "Everyone here can read the target" } };
  await open("access");
  await page().getByText("Every shortcut's readers can read its target too.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Who can read for a storage shortcut lists its readers and says how to limit them", { skip }, async () => {
  await open();
  await page().locator(".sc-table tbody tr").filter({ has: page().locator("b", { hasText: /^landing$/ }) }).click();
  await panel().waitFor();
  await panel().getByRole("tab", { name: "Who can read" }).click();
  await panel().getByText("To limit who reads this storage, add a OneLake data access role on its folder in the Access tab.").waitFor();
  assert.match(await panel().innerText(), /Ana[\s\S]*Workspace Member\s+Connection/);
  assert.deepStrictEqual(app.errors, []);
});

test("the change helpers name each kind of target and who made a change", { skip }, async () => {
  await open();
  const out = await page().evaluate(() => ({
    none: scDefText(null), empty: scDefText({ target: {} }),
    lake: scDefText({ target: { oneLake: { itemId: "lh-bronze", path: "Tables/dbo/orders" } } }),
    lost: scDefText({ target: { oneLake: { itemId: "lh-x", path: "Files/a" } } }),
    s3: scDefText({ target: { amazonS3: { location: "https://s3.amazonaws.com", subpath: "/archive" } } }),
    other: scDefText({ target: { gcs: { location: "gs://b" } } }),
    app: scLastChange({ kind: "added", at: new Date().toISOString(), how: "app" }).textContent,
    by: scLastChange({ kind: "odd", at: new Date().toISOString(), how: "app", by_user: "Ana" }).textContent,
    check: scLastChange({ kind: "broke", at: new Date().toISOString(), how: "check" }).textContent,
  }));
  assert.deepStrictEqual([out.none, out.empty], ["", ""]);
  assert.strictEqual(out.lake, "OneLake: Sales Dev / LH_Bronze / Tables/dbo/orders");
  assert.strictEqual(out.lost, "OneLake: lh-x / Files/a");
  assert.strictEqual(out.s3, "Amazon S3: https://s3.amazonaws.com/archive");
  assert.strictEqual(out.other, "gcs: gs://b");
  assert.match(out.app, /^Added .*in KratosForge$/);
  assert.match(out.by, /^odd .*by Ana$/);
  assert.match(out.check, /^Broke .*health check$/);
  assert.deepStrictEqual(app.errors, []);
});

test("New shortcut: S3 with no connection says so; a folder under Files, a rules warning, and rules that couldn't be checked", { skip }, async () => {
  await open();
  await page().evaluate(() => { shortcutDialog("create"); });
  const d = page().getByRole("dialog", { name: "New shortcut" });
  await d.waitFor();
  await d.getByRole("button", { name: "Amazon S3" }).click();
  await page().waitForFunction(() => document.querySelector('.dialog [aria-label="Connection"]')?.options[0]?.textContent
    === "No Amazon S3 connection yet. Create one in Fabric (Settings > Manage connections).");
  await d.getByRole("button", { name: "ADLS Gen2" }).click();
  await page().waitForFunction(() => document.querySelector('.dialog [aria-label="Connection"]')?.value === "conn-adls");

  db.preflight = () => ({ blocked: [], warnings: ["The target is in another domain."] });
  await d.getByLabel("Put it in").selectOption("Files");
  await d.getByLabel("Folder under Files").fill("/landing/crm/");
  await d.getByLabel("Subpath").fill("raw/crm");
  await d.locator(".warn-box", { hasText: "The target is in another domain." }).waitFor();
  await d.getByRole("button", { name: "Create shortcut" }).click();
  await toastIs("Created shortcut crm in LH_Bronze");
  const made = calls("create_shortcut").at(-1);
  assert.deepStrictEqual([made.where, made.name, made.source.type, made.source.subpath], ["Files/landing/crm", "crm", "adlsGen2", "raw/crm"]);

  db.preflight = () => { throw new Error("The rules lakehouse isn't reachable"); };
  await page().evaluate(() => { toastClose(); shortcutDialog("create"); });
  await d.waitFor();
  await d.getByText("The shortcut rules couldn't be checked", { exact: true }).waitFor();
  assert.strictEqual(await d.getByRole("button", { name: "Create shortcut" }).isDisabled(), true);
  await d.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});

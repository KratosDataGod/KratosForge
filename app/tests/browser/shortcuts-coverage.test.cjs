// OneLake > Shortcuts (shortcuts.js), what shortcuts-tab.test.cjs and shortcut-fixes.test.cjs leave out:
// lakehouses that couldn't be read, a check that finds a break, Export CSV, a broken shortcut's panel and
// the errors in its Who can read and History tabs, Repoint (from a panel, to the same environment's copy,
// and of a storage shortcut), Health with nothing wrong, the check-on-open switch, Access's Add data access
// role, rules that can't be read, and Shortcut needs' actions. The engine is mocked (see harness.cjs).
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ME = "ana@contoso.com";
const at = (s) => new Date(Date.now() + s * 1000).toISOString();
const lakehouse = (ws, wsName, id, name) => ({ workspace_id: ws, workspace_name: wsName, item_id: id, lakehouse_id: id, name, type: "Lakehouse", description: "" });
const CATALOG = {
  workspaces: [{ id: "ws-dev", displayName: "Sales Dev" }, { id: "ws-prod", displayName: "Sales Prod" }],
  items: [lakehouse("ws-dev", "Sales Dev", "lh-bronze", "LH_Bronze"), lakehouse("ws-dev", "Sales Dev", "lh-silver", "LH_Silver"),
    lakehouse("ws-prod", "Sales Prod", "lh-gold", "LH_Gold")],
};
const WS = { "lh-bronze": ["ws-dev", "Sales Dev", "LH_Bronze"], "lh-silver": ["ws-dev", "Sales Dev", "LH_Silver"], "lh-gold": ["ws-prod", "Sales Prod", "LH_Gold"] };
function row(lh, path, name, more = {}) {
  const [ws, wsName, lhName] = WS[lh];
  return { id: `${lh}|${path}/${name}`, name, path, full_path: `${path}/${name}`, kind: path.startsWith("Tables") ? "table" : "folder",
    target_type: "OneLake", target: "Tables/dbo/orders", workspace_id: "ws-dev", item_id: "lh-bronze", target_path: "Tables/dbo/orders",
    exposure: "identity", lakehouse_id: lh, host_workspace_id: ws, workspace_name: wsName, lakehouse_name: lhName,
    health: "healthy", health_detail: "", checked_at: at(-3600), first_seen: at(-86400), host_env: ws === "ws-prod" ? "prod" : "dev", target_env: "dev",
    product: null, risks: [], same_env_target: null, used_by: [], last_change: null, ...more };
}
const ADLS = { target_type: "ADLS Gen2", target: "https://raw.dfs.core.windows.net/crm", location: "https://raw.dfs.core.windows.net", subpath: "/crm",
  connection_id: "conn-adls", exposure: "connection", item_id: null, workspace_id: null, target_path: null, target_env: "" };
const CROSS = { kind: "cross_env", text: "Prod reads Dev: Prod depends on data that isn't released" };
const freshRows = () => [
  row("lh-silver", "Tables/dbo", "orders", { health: "broken", health_detail: "The target table isn't there any more", checked_at: "",
    used_by: [{ entity: "silver.orders", how: "reads it" }], product: { name: "Orders", status: "deprecated", access: "open" } }),
  row("lh-gold", "Tables/dbo", "prices", { risks: [{ ...CROSS }], target_env: "dev",
    same_env_target: { env: "prod", workspace_id: "ws-prod", workspace_name: "Sales Prod", lakehouse_id: "lh-gold", lakehouse_name: "LH_Gold" } }),
  row("lh-gold", "Tables/dbo", "costs", { risks: [{ ...CROSS }], target_env: "dev" }),
  row("lh-bronze", "Files", "landing", { ...ADLS }),
];

let db;
let app;
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);
const copy = (x) => JSON.parse(JSON.stringify(x));
const counts = (rows) => ({ total: rows.length, lakehouses: 3, workspaces: 2, broken: rows.filter((s) => s.health === "broken").length,
  unreachable: 0, not_checked: 0, connection: 1, risky: 2, changed_7d: 0, waiting: 0 });

before(async () => {
  if (skip) return;
  app = await openApp({
    storage: { "fd.shortcuts.checkOnOpen": "0" },
    mocks: {
      readiness_check: () => [],
      catalog: () => CATALOG,
      list_tables: ({ lakehouse_id }) => ({ "lh-bronze": { dbo: ["orders", "customers"] }, "lh-silver": { dbo: ["orders"] }, "lh-gold": { dbo: ["prices"] } }[lakehouse_id] || {}),
      shortcut_center: () => (db.centerReason ? { pending: false, reason: db.centerReason }
        : { pending: false, rows: copy(db.rows), counts: counts(db.rows), errors: db.errors, problems: db.problems, needs: db.needs, requests: [], checked_at: db.checkedAt, admin: false }),
      shortcut_check: ({ items }) => ({ results: items.map((i) => ({ id: i.id, health: i.id.endsWith("costs") ? "broken" : "healthy", detail: i.id.endsWith("costs") ? "Gone" : "" })),
        changes: items.some((i) => i.id.endsWith("costs")) ? [{ kind: "broke", name: "costs" }] : [] }),
      shortcut_export: ({ ids }) => ({ rows: ids.length, path: "C:\\Users\\ana\\Downloads\\shortcuts.csv", notes: ["Paths are as Fabric shows them."] }),
      shortcut_readers: () => { throw new Error("PermissionError: You can't read the workspace roles."); },
      shortcut_changes: () => { throw new Error("The platform isn't reachable"); },
      shortcut_access: () => ({ pending: false, items: { "lh-bronze|Files/landing": { mode: "connection", here: 4, target: null, gap: 4, missing: [], text: "4 read the storage through the connection's account" } } }),
      shortcut_rules: () => { throw new Error("The central lakehouse isn't set up"); },
      shortcut_preflight: () => ({ blocked: [], warnings: [] }),
      shortcut_repoint: () => ({ ok: true, problems: db.repointProblems }),
      shortcut_connections: () => [{ id: "conn-adls", name: "Raw storage", type: "adlsGen2", path: "https://raw.dfs.core.windows.net" },
        { id: "conn-s3", name: "Archive", type: "amazonS3", path: "https://s3.amazonaws.com/archive" }],
      shortcut_find_moved: () => ({ candidate: null, found: [] }),
      access_lakehouse: () => { throw new Error("Not in this test"); },
    },
  });
});

after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  db = { rows: freshRows(), errors: {}, problems: [], needs: [], checkedAt: at(-3600), centerReason: null, repointProblems: [] };
  await page().evaluate(() => { closeDialog(); document.getElementById("toast").textContent = ""; go("home"); });
  app.calls.length = 0;
  app.errors.length = 0;
});

async function open(view = "all", ready = ".sc-body .sc-pad") {
  await page().evaluate(([v, me]) => {
    state.user = me; catalogPage.tab = "shortcuts";
    Object.assign(scPage, { view: v, data: null, selected: null, access: null, changes: null, query: "", ws: "all", type: "all", health: "all", panelTab: "overview" });
    go("onelake");
  }, [view, ME]);
  await page().waitForSelector(ready);
}
const toastIs = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);
const panel = () => page().locator(".preview-panel.open");

test("a read that failed says why; lakehouses that couldn't be read are counted", { skip }, async () => {
  db.centerReason = "The platform isn't set up yet";
  await open("all", ".sc-body .problems");
  assert.match(await page().locator(".sc-body .problems").innerText(), /Couldn't read everything\s+The platform isn't set up yet/);
  assert.match(await page().locator(".sc-body").innerText(), /No shortcuts in any lakehouse yet\. Use New shortcut\./);
  db.centerReason = null;
  db.errors = { "lh-x": "paused", "lh-y": "403" };
  db.problems = ["Sales Test couldn't be listed"];
  await page().getByRole("button", { name: "Refresh" }).click();
  await page().getByText("2 lakehouses couldn't be read (a paused capacity, or no access).").waitFor();
  assert.match(await page().locator(".sc-body .problems").innerText(), /Sales Test couldn't be listed/);
});

test("a check that finds a break says which, and the tile goes back to every shortcut", { skip }, async () => {
  await open();
  await page().getByRole("button", { name: "Check all now" }).first().click();
  await toastIs("1 shortcut broke: costs");
  assert.strictEqual(await page().locator("#toast.error").count(), 1);
  await page().getByLabel("Health").selectOption("broken");
  await page().waitForFunction(() => document.querySelectorAll(".sc-table tbody tr").length === 1);
  await page().locator(".plat-stat", { hasText: "Shortcuts you can see" }).click();
  await page().waitForFunction(() => document.querySelectorAll(".sc-table tbody tr").length === 4);
});

test("Export CSV saves the shortcuts the filters show", { skip }, async () => {
  await open();
  await page().getByLabel("Search shortcuts").fill("landing");
  await page().waitForFunction(() => document.querySelectorAll(".sc-table tbody tr").length === 1);
  await page().getByRole("button", { name: "Export CSV" }).click();
  await toastIs("Saved 1 shortcut to C:\\Users\\ana\\Downloads\\shortcuts.csv. Paths are as Fabric shows them.");
  assert.deepStrictEqual(calls("shortcut_export")[0].ids, ["lh-bronze|Files/landing"]);
  assert.strictEqual(calls("shortcut_export")[0].lakehouses.length, 3);
});

test("a broken shortcut's panel says what fails until it's fixed; its other tabs show what couldn't be read", { skip }, async () => {
  await open();
  await page().locator(".sc-table tbody tr").filter({ has: page().locator("b", { hasText: /^orders$/ }) }).click();
  await panel().waitFor();
  const text = await panel().innerText();
  assert.match(text, /^Broken$/m);
  assert.match(text, /The target table isn't there any more/);
  assert.match(text, /Runs of silver\.orders fail until it's fixed\./);
  assert.match(text, /Data product\s+Orders \(deprecated, open\)/);
  assert.match(await page().locator(".sc-table tbody tr.selected").innerText(), /Data product Orders \(deprecated\)/);
  await panel().getByRole("tab", { name: "Who can read" }).click();
  await panel().getByText("Couldn't read who can read this shortcut").waitFor();
  await panel().getByRole("tab", { name: "History" }).click();
  await panel().getByText("Couldn't read the history").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Repoint from a panel points a OneLake shortcut at another table after a danger confirm", { skip }, async () => {
  await open();
  await page().locator(".sc-table tbody tr").filter({ has: page().locator("b", { hasText: /^orders$/ }) }).click();
  await panel().getByRole("button", { name: "Repoint" }).click();
  const dialog = page().getByRole("dialog", { name: "Repoint orders" });
  assert.match(await dialog.innerText(), /Now: LH_Bronze \/ Tables\/dbo\/orders/);
  assert.strictEqual(await dialog.getByLabel("Target lakehouse").inputValue(), "lh-bronze");
  await dialog.getByLabel("Target lakehouse").selectOption("lh-silver");
  await dialog.locator("select").filter({ has: page().locator("option", { hasText: "dbo.orders" }) }).last().waitFor();
  await dialog.getByRole("button", { name: "Repoint" }).click();
  const ask = page().getByRole("dialog", { name: "Repoint orders?" });
  assert.match(await ask.getByRole("button", { name: "Repoint" }).getAttribute("class"), /danger/);
  db.repointProblems = ["The old one was kept as orders_old"];
  await ask.getByRole("button", { name: "Repoint" }).click();
  await toastIs("Repointed orders; The old one was kept as orders_old");
  const sent = calls("shortcut_repoint")[0];
  assert.deepStrictEqual([sent.workspace_id, sent.item_id, sent.path, sent.name, sent.source.type, sent.source.item_id],
    ["ws-dev", "lh-silver", "Tables/dbo", "orders", "oneLake", "lh-silver"]);
});

test("a storage shortcut repoints with its connection, location and subpath", { skip }, async () => {
  await open();
  await page().locator(".sc-table tbody tr", { hasText: "landing" }).click();
  await panel().getByRole("button", { name: "Repoint" }).click();
  const dialog = page().getByRole("dialog", { name: "Repoint landing" });
  assert.match(await dialog.innerText(), /Now: https:\/\/raw\.dfs\.core\.windows\.net\/crm/);
  await dialog.getByLabel("Connection").locator("option", { hasText: "Raw storage" }).waitFor({ state: "attached" });
  assert.strictEqual(await dialog.getByLabel("Connection").inputValue(), "conn-adls");
  assert.strictEqual(await dialog.getByLabel("Connection").locator("option").count(), 1);
  await dialog.getByLabel("Subpath").fill("/crm2");
  await dialog.getByRole("button", { name: "Repoint" }).click();
  await page().getByRole("dialog", { name: "Repoint landing?" }).getByRole("button", { name: "Repoint" }).click();
  await toastIs("Repointed landing");
  assert.deepStrictEqual(calls("shortcut_repoint")[0].source, { type: "adlsGen2", connection_id: "conn-adls", location: "https://raw.dfs.core.windows.net", subpath: "/crm2" });
});

test("a shortcut crossing environments repoints to the same environment's copy; without a copy it opens Repoint", { skip }, async () => {
  await open("health");
  const card = page().locator(".sc-col .card", { hasText: "Worth a look, not broken" });
  await card.waitFor();
  await card.getByRole("button", { name: "Repoint to Prod's LH_Gold" }).click();
  const ask = page().getByRole("dialog", { name: "Repoint prices?" });
  assert.match(await ask.innerText(), /now points at LH_Gold in Sales Prod, Prod's copy/);
  await ask.getByRole("button", { name: "Repoint" }).click();
  await toastIs("Repointed prices");
  assert.deepStrictEqual(calls("shortcut_repoint")[0].source, { type: "oneLake", workspace_id: "ws-prod", item_id: "lh-gold", path: "Tables/dbo/orders" });
  await page().locator(".sc-col .card", { hasText: "Worth a look, not broken" }).locator(".sc-item", { hasText: "costs" }).getByRole("button", { name: "Repoint" }).click();
  await page().getByRole("dialog", { name: "Repoint costs" }).waitFor();
});

test("Health with nothing wrong says so; the check-on-open switch is remembered", { skip }, async () => {
  db.rows = [row("lh-silver", "Tables/dbo", "fine")];
  await open("health");
  await page().getByText("Every shortcut that could be checked works.").waitFor();
  assert.match(await page().locator(".sc-two").innerText(), /ago: 1 healthy, 0 broken, 0 you can't check, 0 not checked\./);
  await page().getByLabel("Check every shortcut when this page opens").check();
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.shortcuts.checkOnOpen")), "1");
  await page().getByLabel("Check every shortcut when this page opens").uncheck();
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.shortcuts.checkOnOpen")), "0");
  db.checkedAt = null;
  await open("health");
  await page().getByText("Nothing checked yet.").waitFor();
  await page().getByText("Select Check all now.").waitFor();
});

test("Access offers a data access role for a storage shortcut, and says when the rules can't be read", { skip }, async () => {
  await open("access");
  await page().getByText("Couldn't read the shortcut rules: The central lakehouse isn't set up").waitFor();
  const r = page().locator("table.list tbody tr", { hasText: "landing" });
  await r.waitFor();
  assert.match(await r.innerText(), /4\s+Connection\s+4 read the storage/);
  await r.getByRole("button", { name: "Add data access role" }).click();
  await page().waitForFunction(() => catalogPage.tab === "access" && accessPage.lakehouse === "lh-bronze");
});

test("Shortcut needs offer Repoint, the crossing fix, or Open, by what each needs", { skip }, async () => {
  db.needs = [
    { kind: "deprecated", id: "lh-silver|Tables/dbo/orders", title: "orders reads a deprecated product", detail: "In Sales Dev / LH_Silver" },
    { kind: "cross_env", id: "lh-gold|Tables/dbo/costs", title: "costs crosses environments", detail: "In Sales Prod / LH_Gold" },
    { kind: "broken_in_use", id: "lh-bronze|Files/landing", title: "landing is broken and in use", detail: "In Sales Dev / LH_Bronze" },
    { kind: "deprecated", id: "lh-gone|Tables/x", title: "a shortcut that isn't listed", detail: "" },
  ];
  await open("requests");
  const items = page().locator(".sc-need");
  await items.first().waitFor();
  assert.strictEqual(await items.count(), 4);
  assert.deepStrictEqual(await items.nth(0).getByRole("button").allTextContents(), ["Repoint"]);
  assert.deepStrictEqual(await items.nth(1).getByRole("button").allTextContents(), ["Repoint"]);
  assert.deepStrictEqual(await items.nth(2).getByRole("button").allTextContents(), ["Open"]);
  assert.strictEqual(await items.nth(3).getByRole("button").count(), 0);
  await items.nth(2).getByRole("button", { name: "Open" }).click();
  await panel().waitFor();
  assert.deepStrictEqual(await page().evaluate(() => [scPage.view, scPage.selected]), ["all", "lh-bronze|Files/landing"]);
  db.needs = [];
  await open("requests");
  await page().getByText("Nothing needs a shortcut change.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

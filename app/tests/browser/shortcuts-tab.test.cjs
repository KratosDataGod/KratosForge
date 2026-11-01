// #1423: OneLake > Shortcuts (shortcuts.js; the engine side is sidecar/fabric_desktop/shortcut_service.py),
// see harness.cjs for how to run it. Every shortcut in four lakehouses across two workspaces, with its
// health, who reads it and what changed. Each view (All, Health, Access, Changes, Requests) shows what
// the engine returned, and the filters narrow it. New shortcut runs the rules check (shortcut_preflight)
// first and sends exactly the lakehouse, place, name and target picked; a click before the check answered
// waits for it. Remove and Undo ask first with a danger confirm, and Cancel sends nothing; Recreate asks
// too. Check, Request and Save rules send exactly what was entered. An engine refusal shows a plain
// message and the list stays as it was. Opening each view sends no write, and axe finds nothing serious
// on each view and dialog.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const ME = "ana@contoso.com";
const READS = ["shortcut_center", "shortcut_access", "shortcut_rules", "shortcut_changes", "shortcut_readers"];
const WRITES = ["create_shortcut", "delete_shortcut", "shortcut_undo", "shortcut_check", "shortcut_request", "shortcut_save_rules",
  "shortcut_repoint", "shortcut_repoint_many", "shortcut_accept_risk", "shortcut_withdraw_risk", "platform_decide", "platform_withdraw"];
const DENIED = "HTTPError: 403 Client Error: Forbidden for url: https://api.fabric.microsoft.com/v1/workspaces/ws-dev/items/lh-silver/shortcuts/Tables/dbo/customers";
const PERMISSION = "You don't have permission for that in this workspace. A workspace admin can make you a Contributor or above.";

const at = (s) => new Date(Date.now() + s * 1000).toISOString().replace(/\.\d{3}Z$/, "+00:00");

// Lakehouses as service.py catalog(all_types=True) lists them.
const lakehouse = (ws, wsName, id, name) => ({ workspace_id: ws, workspace_name: wsName, item_id: id, lakehouse_id: id, name, type: "Lakehouse", description: "" });
const CATALOG = {
  workspaces: [{ id: "ws-dev", displayName: "Sales Dev", type: "Workspace" }, { id: "ws-prod", displayName: "Sales Prod", type: "Workspace" }],
  items: [lakehouse("ws-dev", "Sales Dev", "lh-bronze", "LH_Bronze"), lakehouse("ws-dev", "Sales Dev", "lh-silver", "LH_Silver"),
    lakehouse("ws-prod", "Sales Prod", "lh-gold", "LH_Gold")],
  skipped: [], favorites: [],
};
const WS_NAME = { "ws-dev": "Sales Dev", "ws-prod": "Sales Prod" };
const LH = { "lh-bronze": ["ws-dev", "LH_Bronze"], "lh-silver": ["ws-dev", "LH_Silver"], "lh-gold": ["ws-prod", "LH_Gold"] };
// service.py list_tables: {schema: [table]}
const TABLES = { "lh-bronze": { dbo: ["customers", "orders"] }, "lh-silver": { dbo: ["customers", "orders"] }, "lh-gold": { dbo: ["sales"] } };

// One row as shortcut_service.py shortcut_center() returns it: shortcuts.py describe() of the REST
// shortcut, plus where it lives, used_by (estate_service.py), and the platform's health, risks and last change.
function row(lh, path, name, target, more = {}) {
  const [ws, lhName] = LH[lh];
  const full = `${path}/${name}`;
  const t = target.oneLake
    ? { target_type: "OneLake", target: target.oneLake.path, rest_target: target, workspace_id: target.oneLake.workspaceId,
      item_id: target.oneLake.itemId, target_path: target.oneLake.path, exposure: "identity",
      target_workspace_name: WS_NAME[target.oneLake.workspaceId], target_lakehouse_name: LH[target.oneLake.itemId][1] }
    : { target_type: "ADLS Gen2", target: `${target.adlsGen2.location.replace(/\/$/, "")}/${target.adlsGen2.subpath.replace(/^\//, "")}`,
      rest_target: target, location: target.adlsGen2.location, subpath: target.adlsGen2.subpath, connection_id: target.adlsGen2.connectionId,
      exposure: "connection", target_env: "" };
  return { id: `${lh}|${full}`, name, path, full_path: full, kind: path.startsWith("Tables") ? "table" : "folder", ...t,
    lakehouse_id: lh, host_workspace_id: ws, workspace_name: WS_NAME[ws], lakehouse_name: lhName,
    health: "healthy", health_detail: "", checked_at: at(-3600), first_seen: at(-86400 * 40), host_env: ws === "ws-prod" ? "prod" : "dev",
    target_env: t.target_env ?? (target.oneLake.workspaceId === "ws-prod" ? "prod" : "dev"),
    product: null, risks: [], same_env_target: null, used_by: [], last_change: null, ...more };
}
const oneLake = (lh, path) => ({ oneLake: { workspaceId: LH[lh][0], itemId: lh, path } });
const CROSS = { kind: "cross_env", text: "Prod reads Dev: Prod depends on data that isn't released" };

function freshRows() {
  return [
    row("lh-silver", "Tables/dbo", "customers", oneLake("lh-bronze", "Tables/dbo/customers"),
      { used_by: [{ entity: "silver.customers", how: "has the same name, so its runs write into the shortcut's target" }],
        last_change: { at: at(-3600), kind: "added", by_user: ME, how: "app" } }),
    row("lh-silver", "Tables/dbo", "orders", oneLake("lh-bronze", "Tables/dbo/orders"),
      { health: "broken", health_detail: "The target table or folder isn't there any more (removed, renamed or moved)" }),
    row("lh-bronze", "Files", "landing", { adlsGen2: { location: "https://salesraw.dfs.core.windows.net", subpath: "/raw/crm", connectionId: "conn-adls" } },
      { health: "unreachable", health_detail: "Your sign-in can't list it: the connection's account reads it for everyone" }),
    row("lh-gold", "Tables/dbo", "prices", oneLake("lh-silver", "Tables/dbo/customers"), { risks: [{ ...CROSS }] }),
  ];
}
// Platform requests (central/core.py request()), kind shortcut, as list_requests returns them to ME.
const REQUESTS = [
  { id: "rq-1", kind: "shortcut", target: "lh-gold|Tables/dbo/returns", project: "", reason: "Finance needs returns in Gold",
    requested_by: "bob@contoso.com", requested_at: at(-7200), status: "waiting", decided_by: "", decided_at: "", note: "", can_decide: true, mine: false,
    detail: { workspace_id: "ws-prod", item_id: "lh-gold", where: "Tables/dbo", name: "returns", lakehouse_name: "LH_Gold", workspace_name: "Sales Prod",
      source: { type: "oneLake", workspace_id: "ws-dev", item_id: "lh-silver", path: "Tables/dbo/orders" }, target_workspace_name: "Sales Dev", target_type: "OneLake", product_id: "" } },
  { id: "rq-2", kind: "shortcut", target: "lh-silver|Files/budget", project: "", reason: "Budget files for planning",
    requested_by: ME, requested_at: at(-86400), status: "waiting", decided_by: "", decided_at: "", note: "", can_decide: false, mine: true,
    detail: { workspace_id: "ws-dev", item_id: "lh-silver", where: "Files", name: "budget", lakehouse_name: "LH_Silver", workspace_name: "Sales Dev",
      source: { type: "oneLake", workspace_id: "ws-prod", item_id: "lh-gold", path: "Files/budget" }, target_workspace_name: "Sales Prod", target_type: "OneLake", product_id: "" } },
];
// Changes as central/core.py shortcut_changes() returns them: _change_out() rows with undoable.
const def = (lh, path, name, target) => ({ workspace_id: LH[lh][0], lakehouse_id: lh, path, name, target });
function freshChanges() {
  return [
    { id: "ch-add", at: at(-3600), kind: "added", shortcut_id: "lh-silver|Tables/dbo/customers", workspace_name: "Sales Dev", lakehouse_name: "LH_Silver",
      name: "customers", before: null, after: def("lh-silver", "Tables/dbo", "customers", oneLake("lh-bronze", "Tables/dbo/customers")),
      by_user: ME, how: "app", note: "", undoable: true },
    { id: "ch-rm", at: at(-7200), kind: "removed", shortcut_id: "lh-silver|Tables/dbo/old_orders", workspace_name: "Sales Dev", lakehouse_name: "LH_Silver",
      name: "old_orders", before: def("lh-silver", "Tables/dbo", "old_orders", oneLake("lh-bronze", "Tables/dbo/orders")), after: null,
      by_user: "dana@contoso.com", how: "app", note: "", undoable: true },
    { id: "ch-rp", at: at(-86400 * 3), kind: "repointed", shortcut_id: "lh-gold|Tables/dbo/prices", workspace_name: "Sales Prod", lakehouse_name: "LH_Gold",
      name: "prices", before: def("lh-gold", "Tables/dbo", "prices", oneLake("lh-bronze", "Tables/dbo/customers")),
      after: def("lh-gold", "Tables/dbo", "prices", oneLake("lh-silver", "Tables/dbo/customers")), by_user: "", how: "scan", note: "", undoable: true },
    { id: "ch-broke", at: at(-86400 * 10), kind: "broke", shortcut_id: "lh-silver|Tables/dbo/orders", workspace_name: "Sales Dev", lakehouse_name: "LH_Silver",
      name: "orders", before: null, after: null, by_user: "", how: "check", note: "The target table or folder isn't there any more", undoable: false },
  ];
}
// central/core.py DEFAULT_SHORTCUT_RULES and shortcut_center.py RULE_TEXT.
const RULES = { cross_env: "block", storage_role: "warn", deprecated_product: "warn", connections: "off", allowed_connections: [], who_creates: "anyone" };
const RULE_TEXT = {
  cross_env: "A shortcut may not point at another environment (Dev or Test at Prod, or Prod at Dev or Test)",
  storage_role: "A storage shortcut should have a data access role on its folder",
  deprecated_product: "A shortcut should not point at a deprecated data product",
  connections: "Storage shortcuts use only the allowed connections",
  who_creates: "Who may create shortcuts from KratosForge",
};

let db;
const fail = {};   // method -> the engine's error text
const hold = {};   // method -> ms to wait before answering
const copy = (x) => JSON.parse(JSON.stringify(x));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function reset() {
  db = { rows: freshRows(), changes: freshChanges(), rules: { ...RULES } };
  for (const o of [fail, hold]) for (const k of Object.keys(o)) delete o[k];
}
reset();
const guard = (method, fn) => async (p) => {
  if (hold[method]) await sleep(hold[method]);
  if (fail[method]) throw new Error(fail[method]);
  return fn(p);
};

// shortcut_center.counts()
function counts(rows) {
  return { total: rows.length, lakehouses: new Set(rows.map((s) => s.lakehouse_id)).size, workspaces: new Set(rows.map((s) => s.host_workspace_id)).size,
    broken: rows.filter((s) => s.health === "broken").length, unreachable: rows.filter((s) => s.health === "unreachable").length,
    not_checked: rows.filter((s) => s.health === "not_checked").length, connection: rows.filter((s) => s.exposure === "connection").length,
    risky: rows.filter((s) => s.risks.some((r) => !r.accepted)).length,
    changed_7d: db.changes.filter((c) => ["added", "removed", "repointed"].includes(c.kind) && c.at >= at(-86400 * 7)).length,
    waiting: REQUESTS.filter((r) => r.status === "waiting" && r.can_decide).length };
}
// shortcut_center.check_rules() for a shortcut from Dev: crossing into Prod is blocked by the default rule.
const preflightOf = ({ workspace_id, source }) => {
  const host = workspace_id === "ws-prod" ? "prod" : "dev";
  const tgt = source.type === "oneLake" ? (source.workspace_id === "ws-prod" ? "prod" : "dev") : "";
  const blocked = [], warnings = [];
  if (tgt && tgt !== host && db.rules.cross_env !== "off") {
    (db.rules.cross_env === "block" ? blocked : warnings).push(`It points from ${host[0].toUpperCase() + host.slice(1)} at ${tgt[0].toUpperCase() + tgt.slice(1)}. Point it at the same environment's copy`);
  }
  if (source.type !== "oneLake") warnings.push("Everyone who can read this lakehouse will read the storage with the connection's account. Add a data access role on the folder to limit it");
  return { blocked, warnings };
};

const mocks = {
  readiness_check: () => [],
  catalog: () => CATALOG,
  list_tables: ({ lakehouse_id }) => TABLES[lakehouse_id] || {},
  // shortcut_service.py
  shortcut_center: guard("shortcut_center", () => ({ pending: false, rows: copy(db.rows), errors: {}, problems: [], counts: counts(db.rows),
    needs: [{ kind: "cross_env", id: "lh-gold|Tables/dbo/prices", title: `prices: ${CROSS.text}`, detail: "In Sales Prod / LH_Gold. Repoint it at the same environment." },
      { kind: "request", id: "rq-1", title: "bob@contoso.com asks for shortcut returns", detail: "In Sales Prod / LH_Gold: Finance needs returns in Gold" }],
    requests: copy(REQUESTS), checked_at: at(-3600), admin: true })),
  shortcut_check: guard("shortcut_check", ({ items }) => ({ results: items.map((i) => i.id === "lh-silver|Tables/dbo/orders"
    ? { id: i.id, health: "healthy", detail: "" } : { id: i.id, health: db.rows.find((r) => r.id === i.id)?.health || "healthy", detail: db.rows.find((r) => r.id === i.id)?.health_detail || "" }),
  changes: items.some((i) => i.id === "lh-silver|Tables/dbo/orders") ? [{ kind: "fixed", name: "orders" }] : [], problems: [] })),
  shortcut_access: guard("shortcut_access", () => ({ pending: false, items: {
    "lh-silver|Tables/dbo/customers": { mode: "identity", here: 5, target: 3, gap: 2, missing: [{ id: "u4" }, { id: "u5" }], partial: false,
      text: "2 of 5 see it empty: they have no access to the target" },
    "lh-silver|Tables/dbo/orders": { mode: "identity", here: 5, target: 5, gap: 0, missing: [], partial: false, text: "Matches" },
    "lh-bronze|Files/landing": { mode: "connection", here: 4, target: null, gap: 4, missing: [], partial: false, text: "4 read the storage through the connection's account" },
    "lh-gold|Tables/dbo/prices": { mode: "identity", here: 2, target: 2, gap: 0, missing: [], partial: false, text: "Matches" } } })),
  shortcut_readers: guard("shortcut_readers", () => ({ mode: "identity", here: 2, target: 1, gap: 1, partial: false, problem: null,
    missing: [{ id: "u2", name: "Bob", email: "bob@contoso.com", how: "Viewer, through a data access role" }],
    readers: [{ id: "u1", name: "Ana", email: ME, how: "Admin" }, { id: "u2", name: "Bob", email: "bob@contoso.com", how: "Viewer, through a data access role" }],
    text: "1 of 2 see it empty: they have no access to the target" })),
  shortcut_rules: guard("shortcut_rules", () => ({ rules: { ...db.rules }, text: RULE_TEXT, admin: true, problems: [] })),
  shortcut_save_rules: guard("shortcut_save_rules", ({ rules }) => { db.rules = { ...rules }; return { rules: { ...db.rules }, text: RULE_TEXT, admin: true, problems: [] }; }),
  shortcut_changes: guard("shortcut_changes", ({ days = 30, shortcut = "" }) => ({ changes: copy(db.changes.filter((c) => c.at >= at(-86400 * days)
    && (!shortcut || c.shortcut_id === shortcut))), problems: [] })),
  shortcut_connections: () => [{ id: "conn-adls", name: "Sales raw storage", type: "adlsGen2", path: "https://salesraw.dfs.core.windows.net" }],
  shortcut_preflight: guard("shortcut_preflight", preflightOf),
  shortcut_undo: guard("shortcut_undo", ({ change_id }) => {
    const c = db.changes.find((x) => x.id === change_id);
    if (!c) throw new Error("LookupError: That change isn't in the history any more");
    return { ok: true, problems: [] };
  }),
  shortcut_request: guard("shortcut_request", (p) => ({ id: "rq-new", kind: "shortcut", target: `${p.lakehouse_name}/${p.where}/${p.name}`, status: "waiting" })),
  // estate_service.py: the lakehouse's shortcuts after the change
  create_shortcut: guard("create_shortcut", (p) => {
    const t = { oneLake: { workspaceId: p.source.workspace_id, itemId: p.source.item_id, path: p.source.path } };
    db.rows.push(row(p.item_id, p.where, p.name, t, { health: "not_checked", checked_at: "" }));
    return db.rows.filter((r) => r.lakehouse_id === p.item_id);
  }),
  delete_shortcut: guard("delete_shortcut", (p) => {
    db.rows = db.rows.filter((r) => !(r.lakehouse_id === p.item_id && r.path === p.path && r.name === p.name));
    return db.rows.filter((r) => r.lakehouse_id === p.item_id);
  }),
};

let baseline;
before(async () => {
  if (skip) return;
  // A check on open is a write (it records health with the platform): this test starts checks itself.
  app = await openApp({ mocks, storage: { "fd.shortcuts.checkOnOpen": "0" } });
  await startupReads(app);
  for (const end = Date.now() + 15000; !["integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await sleep(50)) {
    if (Date.now() > end) throw new Error("the app's startup polls never came");
  }
  baseline = new Set(app.calls.map((c) => c.method));
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  reset();
  await page().evaluate(() => { closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; go("home"); });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const dialog = (name) => page().getByRole("dialog", { name });
async function toastText() {
  const handle = await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; });
  return handle.jsonValue();
}

// Opens a view afresh: the engine read again and every filter cleared.
async function openShortcuts(view) {
  await page().evaluate(([v, me]) => {
    state.user = me; catalogPage.tab = "shortcuts";
    Object.assign(scPage, { view: v, data: null, selected: null, access: null, changes: null, query: "", ws: "all", type: "all", health: "all",
      days: 30, changeKind: "all", panelTab: "overview" });
    go("onelake");
  }, [view, ME]);
  await page().waitForSelector(".sc-body .sc-pad");
}
// The All table's rows: each row's cells as text, once there are n.
async function tableRows(n) {
  const handle = await page().waitForFunction((n) => {
    const trs = [...document.querySelectorAll(".sc-table tbody tr")];
    if (trs.length !== n) return false;
    return trs.map((tr) => [...tr.children].map((td) => td.innerText.trim().replace(/\s+/g, " ")));
  }, n);
  return handle.jsonValue();
}
const names = (rows) => rows.map((r) => r[0].split(" ")[0]);
// A row's name, a cell with no handler of its own: clicking it opens the row's panel.
const openRow = (name) => page().locator(".sc-table tbody tr td:first-child b", { hasText: new RegExp(`^${name}$`) }).click();
const view = (label) => page().locator(".sc-views button", { hasText: label });

test("All lists every shortcut the engine returned, broken first, and the filters narrow it", { skip }, async () => {
  await openShortcuts("all");
  const rows = await tableRows(4);
  assert.deepStrictEqual(names(rows), ["orders", "landing", "prices", "customers"], "broken, can't check, worth a look, then the rest");
  const by = Object.fromEntries(rows.map((r) => [r[0].split(" ")[0], r]));
  assert.deepStrictEqual([by.customers[1], by.customers[3], by.customers[4]], ["Sales Dev LH_Silver", "Healthy", "silver.customers"]);
  assert.match(by.customers[2], /^OneLake ?LH_Bronze Sales Dev · Tables\/dbo\/customers$/);
  assert.match(by.customers[5], /^Added .* by ana@contoso\.com$/);
  assert.match(by.orders[3], /^Broken The target table or folder isn't there any more/);
  assert.match(by.landing[2], /^ADLS Gen2 https:\/\/salesraw\.dfs\.core\.windows\.net\/raw\/crm$/);
  assert.match(by.landing[3], /^Unreachable Your sign-in can't list it/);
  assert.strictEqual(by.prices[1], "Sales Prod LH_Gold");
  assert.match(by.prices[3], /^Healthy Prod reads Dev: Prod depends on data that isn't released$/);
  assert.strictEqual(by.orders[4], "Not used here");
  // The tiles count what the engine counted.
  const tiles = await page().locator(".plat-stats .tile").evaluateAll((ts) => ts.map((t) => [t.querySelector(".plat-n").textContent, t.querySelector(".tt").textContent]));
  assert.deepStrictEqual(tiles, [["4", "Shortcuts you can see"], ["1", "Broken"], ["1", "Read with a connection"], ["3", "Changed in 7 days"], ["1", "Waiting on you"]]);
  assert.deepStrictEqual(await page().locator(".sc-views button").allTextContents(), ["All shortcuts", "Health2", "Access", "Changes", "Requests1"]);
  // Search, then each filter.
  await page().getByRole("textbox", { name: "Search shortcuts" }).fill("salesraw");
  assert.deepStrictEqual(names(await tableRows(1)), ["landing"]);
  await page().getByRole("textbox", { name: "Search shortcuts" }).fill("nothing like this");
  await page().locator(".sc-table", { hasText: "Nothing matches." }).waitFor();
  await page().getByRole("textbox", { name: "Search shortcuts" }).fill("");
  await tableRows(4);
  await page().getByRole("combobox", { name: "Workspace" }).selectOption("ws-prod");
  assert.deepStrictEqual(names(await tableRows(1)), ["prices"]);
  await page().getByRole("combobox", { name: "Workspace" }).selectOption("all");
  await page().getByRole("combobox", { name: "Points at" }).selectOption("ADLS Gen2");
  assert.deepStrictEqual(names(await tableRows(1)), ["landing"]);
  await page().getByRole("combobox", { name: "Points at" }).selectOption("OneLake");
  await tableRows(3);
  await page().getByRole("combobox", { name: "Health" }).selectOption("broken");
  assert.deepStrictEqual(names(await tableRows(1)), ["orders"]);
  await page().getByRole("combobox", { name: "Points at" }).selectOption("all");
  await page().getByRole("combobox", { name: "Health" }).selectOption("risky");
  assert.deepStrictEqual(names(await tableRows(1)), ["prices"]);
  await page().getByRole("combobox", { name: "Health" }).selectOption("healthy");
  assert.deepStrictEqual(names(await tableRows(2)), ["prices", "customers"]);
  // A row opens its panel.
  await page().getByRole("combobox", { name: "Health" }).selectOption("all");
  await openRow("customers");
  const panel = page().locator(".preview-panel.open");
  await panel.waitFor();
  const text = await panel.innerText();
  assert.match(text, /Sales Dev \/ LH_Silver \/ Tables\/dbo/);
  assert.match(text, /Lakehouse\s+Sales Dev \/ LH_Bronze/);
  assert.match(text, /silver\.customers has the same name/);
});

test("Health groups broken and unreachable shortcuts by cause, and lists what's worth a look", { skip }, async () => {
  await openShortcuts("health");
  const card = (text) => page().locator(".sc-col .card.pad", { has: page().locator("b", { hasText: text }) });
  const broken = card("The target table or folder isn't there any more");
  await broken.waitFor();
  assert.deepStrictEqual(await broken.locator(".sc-item b").allTextContents(), ["orders"]);
  assert.strictEqual(await broken.locator(".pill-s.fail").textContent(), "1");
  assert.match(await broken.locator(".sc-item").innerText(), /Sales Dev \/ LH_Silver[\s\S]*Nothing in the open project reads it/);
  const unreachable = card("Your sign-in can't list it");
  assert.deepStrictEqual(await unreachable.locator(".sc-item b").allTextContents(), ["landing"]);
  const risky = card("Worth a look, not broken");
  assert.deepStrictEqual(await risky.locator(".sc-item b").allTextContents(), ["prices"]);
  assert.match(await risky.innerText(), /Prod reads Dev: Prod depends on data that isn't released/);
  assert.match(await page().locator(".sc-side").innerText(), /2 healthy, 1 broken, 1 you can't check, 0 not checked\./);
});

test("Access lists where readers and targets don't match, and the rules as saved", { skip }, async () => {
  await openShortcuts("access");
  const box = page().locator(".card.pad", { has: page().locator("b", { hasText: "Where readers and targets don't match" }) });
  await box.locator("table").waitFor();
  const rows = await box.locator("tbody tr").evaluateAll((trs) => trs.map((tr) => [...tr.children].slice(0, 4).map((td) => td.innerText.trim().replace(/\s+/g, " "))));
  assert.deepStrictEqual(rows, [
    ["customers Sales Dev / LH_Silver", "5", "3", "2 of 5 see it empty: they have no access to the target"],
    ["landing Sales Dev / LH_Bronze", "4", "Connection", "4 read the storage through the connection's account"],
  ]);
  const rules = page().locator(".card.pad", { has: page().locator("b", { hasText: "Shortcut rules" }) });
  await rules.getByRole("button", { name: "Save rules" }).waitFor();
  assert.strictEqual(await rules.getByRole("combobox", { name: RULE_TEXT.cross_env }).inputValue(), "block");
  assert.strictEqual(await rules.getByRole("combobox", { name: RULE_TEXT.storage_role }).inputValue(), "warn");
  assert.strictEqual(await rules.getByRole("combobox", { name: RULE_TEXT.who_creates }).inputValue(), "anyone");
});

// The Changes list once it holds n changes: each one's text.
async function changeItems(n) {
  const handle = await page().waitForFunction((n) => {
    const els = [...document.querySelectorAll(".sc-changes .sc-change")];
    return els.length === n && els.map((e) => e.innerText.replace(/\s+/g, " ").trim());
  }, n);
  return handle.jsonValue();
}
const changeRow = (name) => page().locator(".sc-changes .sc-change", { has: page().locator("b", { hasText: new RegExp(`^${name}$`) }) });

test("Changes lists the history the engine returned, and the period and kind narrow it", { skip }, async () => {
  const from = app.calls.length;
  await openShortcuts("changes");
  const all = await changeItems(4);
  assert.deepStrictEqual(app.calls.slice(from).filter((c) => c.method === "shortcut_changes").map((c) => c.params), [{ days: 30 }]);
  assert.match(all[0], /Added customers in Sales Dev \/ LH_Silver → OneLake: Sales Dev \/ LH_Bronze \/ Tables\/dbo\/customers ana@contoso\.com Undo$/);
  assert.match(all[1], /Removed old_orders in Sales Dev \/ LH_Silver was → OneLake: Sales Dev \/ LH_Bronze \/ Tables\/dbo\/orders dana@contoso\.com Recreate$/);
  assert.match(all[2], /Repointed prices in Sales Prod \/ LH_Gold OneLake: Sales Dev \/ LH_Bronze \/ Tables\/dbo\/customers → OneLake: Sales Dev \/ LH_Silver \/ Tables\/dbo\/customers Seen at scan made outside the app Undo$/);
  assert.match(all[3], /Broke orders in Sales Dev \/ LH_Silver The target table or folder isn't there any more Health check$/);
  await page().getByRole("combobox", { name: "Change" }).selectOption("removed");
  assert.match((await changeItems(1))[0], /Removed old_orders/);
  await page().getByRole("combobox", { name: "Change" }).selectOption("all");
  await changeItems(4);
  const at7 = app.calls.length;
  await page().getByRole("combobox", { name: "Period" }).selectOption("7");
  const week = await changeItems(3);
  assert.deepStrictEqual(app.calls.slice(at7).filter((c) => c.method === "shortcut_changes").map((c) => c.params), [{ days: 7 }]);
  assert.ok(week.every((t) => !/Broke orders/.test(t)));
  await page().getByRole("combobox", { name: "Change" }).selectOption("broke");
  await page().locator(".sc-pad", { hasText: "No changes in this period." }).waitFor();
});

test("Requests shows the requests waiting on you, yours, and the shortcut needs", { skip }, async () => {
  await openShortcuts("requests");
  const waiting = page().locator(".card.pad", { has: page().locator("b", { hasText: /^Waiting on you$/ }) });
  const rows = await waiting.locator("tbody tr").allInnerTexts();
  assert.strictEqual(rows.length, 1);
  assert.match(rows[0].replace(/\s+/g, " "), /^Shortcut returns in Sales Prod \/ LH_Gold Finance needs returns in Gold bob@contoso\.com .*Approve Decline$/);
  const mine = page().locator(".card.pad", { has: page().locator("b", { hasText: /^Your requests$/ }) });
  const own = await mine.locator("tbody tr").allInnerTexts();
  assert.strictEqual(own.length, 1);
  assert.match(own[0].replace(/\s+/g, " "), /^Shortcut budget in Sales Dev \/ LH_Silver Budget files for planning ana@contoso\.com .*Waiting Withdraw$/);
  const needs = await page().locator(".sc-need").allInnerTexts();
  assert.deepStrictEqual(needs.map((t) => t.split("\n")[0]), [`prices: ${CROSS.text}`], "a request is listed once, under Waiting on you");
});

// Fills New shortcut (or Request a shortcut) with a OneLake target.
async function fillDialog(d, { host, where, target, table, name, why }) {
  await d.getByRole("combobox", { name: "Lakehouse", exact: true }).selectOption(host);
  await d.getByRole("combobox", { name: "Put it in" }).selectOption(where);
  await d.getByRole("combobox", { name: "Target lakehouse" }).selectOption(target);
  await d.getByRole("combobox", { name: "Table" }).selectOption(table);
  await d.getByRole("textbox", { name: "Name" }).fill(name);
  if (why !== undefined) await d.getByRole("textbox", { name: "Why" }).fill(why);
}
const callIndex = (method, from) => app.calls.findIndex((c, i) => i >= from && c.method === method);

test("New shortcut runs the rules check first and sends exactly the lakehouse, place, name and target picked", { skip }, async () => {
  await openShortcuts("all");
  await tableRows(4);
  const from = app.calls.length;
  await page().getByRole("button", { name: "New shortcut" }).click();
  const d = dialog("New shortcut");
  await d.waitFor();
  await fillDialog(d, { host: "lh-silver", where: "Tables/dbo", target: "lh-bronze", table: "Tables/dbo/orders", name: "bronze_orders" });
  await d.getByRole("button", { name: "Create shortcut" }).click();
  await d.waitFor({ state: "detached" });
  const source = { type: "oneLake", workspace_id: "ws-dev", item_id: "lh-bronze", path: "Tables/dbo/orders" };
  assert.deepStrictEqual(writes(from), [{ method: "create_shortcut", params: { workspace_id: "ws-dev", item_id: "lh-silver", source,
    lakehouse_name: "LH_Silver", workspace_name: "Sales Dev", target_workspace_name: "Sales Dev", where: "Tables/dbo", name: "bronze_orders" } }]);
  // The check of exactly that shortcut was sent before it was made.
  const made = callIndex("create_shortcut", from);
  const checks = app.calls.slice(from, made).filter((c) => c.method === "shortcut_preflight").map((c) => c.params);
  assert.deepStrictEqual(checks.at(-1), { workspace_id: "ws-dev", source, workspace_name: "Sales Dev", target_workspace_name: "Sales Dev" },
    "the rules were checked for this shortcut before it was made");
  assert.match(await toastText(), /^Created shortcut bronze_orders in LH_Silver/);
  const rows = await tableRows(5);
  assert.ok(names(rows).includes("bronze_orders"));
});

test("New shortcut clicked before the rules check answered waits for it, and a blocked one becomes a request", { skip }, async () => {
  await openShortcuts("all");
  await tableRows(4);
  hold.shortcut_preflight = 600;
  const from = app.calls.length;
  await page().getByRole("button", { name: "New shortcut" }).click();
  const d = dialog("New shortcut");
  await d.waitFor();
  // Dev at Prod: the default rule blocks it. Clicked at once, before the check has answered.
  await fillDialog(d, { host: "lh-silver", where: "Tables/dbo", target: "lh-gold", table: "Tables/dbo/sales", name: "gold_sales" });
  await d.getByRole("button", { name: "Create shortcut" }).click();
  await d.getByRole("button", { name: "Send as a request instead" }).waitFor();
  assert.match(await d.innerText(), /The shortcut rules stop this[\s\S]*It points from Dev at Prod/);
  assert.deepStrictEqual(writes(from), [], "nothing is made before the rules answered, nor once they said no");
  delete hold.shortcut_preflight;
  await d.getByRole("button", { name: "Send as a request instead" }).click();
  await d.waitFor({ state: "detached" });
  const source = { type: "oneLake", workspace_id: "ws-prod", item_id: "lh-gold", path: "Tables/dbo/sales" };
  assert.deepStrictEqual(writes(from), [{ method: "shortcut_request", params: { workspace_id: "ws-dev", item_id: "lh-silver", source,
    lakehouse_name: "LH_Silver", workspace_name: "Sales Dev", target_workspace_name: "Sales Prod", where: "Tables/dbo", name: "gold_sales", reason: "" } }]);
  assert.ok(callIndex("shortcut_preflight", from) < callIndex("shortcut_request", from));
});

test("Request a shortcut sends exactly what was entered, with the reason", { skip }, async () => {
  await openShortcuts("requests");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Request a shortcut" }).click();
  const d = dialog("Request a shortcut");
  await d.waitFor();
  await fillDialog(d, { host: "lh-gold", where: "Tables/dbo", target: "lh-silver", table: "Tables/dbo/orders", name: "silver_orders", why: "For the Gold sales model" });
  await d.getByRole("button", { name: "Send request" }).click();
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "shortcut_request", params: { workspace_id: "ws-prod", item_id: "lh-gold",
    source: { type: "oneLake", workspace_id: "ws-dev", item_id: "lh-silver", path: "Tables/dbo/orders" },
    lakehouse_name: "LH_Gold", workspace_name: "Sales Prod", target_workspace_name: "Sales Dev", where: "Tables/dbo", name: "silver_orders",
    reason: "For the Gold sales model" } }]);
  assert.match(await toastText(), /^Asked for shortcut silver_orders\./);
});

test("Remove asks first with a danger confirm; Cancel sends nothing, and Remove sends that shortcut", { skip }, async () => {
  await openShortcuts("all");
  await tableRows(4);
  await openRow("customers");
  const panel = page().locator(".preview-panel.open");
  const from = app.calls.length;
  await panel.getByRole("button", { name: "Remove" }).click();
  const d = dialog("Remove shortcut customers?");
  await d.waitFor();
  assert.match(await d.innerText(), /Only the shortcut in LH_Silver goes\. The data it points at isn't touched\. silver\.customers read it and will fail\./);
  assert.match(await d.getByRole("button", { name: "Remove" }).getAttribute("class"), /danger/);
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  await sleep(300);
  assert.deepStrictEqual(writes(from), []);
  assert.strictEqual((await tableRows(4)).length, 4);
  await panel.getByRole("button", { name: "Remove" }).click();
  await d.getByRole("button", { name: "Remove" }).click();
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "delete_shortcut", params: { workspace_id: "ws-dev", item_id: "lh-silver", path: "Tables/dbo",
    name: "customers", lakehouse_name: "LH_Silver", workspace_name: "Sales Dev" } }]);
  assert.match(await toastText(), /^Removed shortcut customers/);
  assert.deepStrictEqual(names(await tableRows(3)), ["orders", "landing", "prices"]);
});

test("Remove from Health asks first too", { skip }, async () => {
  await openShortcuts("health");
  const from = app.calls.length;
  await page().locator(".sc-item", { has: page().locator("b", { hasText: /^orders$/ }) }).getByRole("button", { name: "Remove" }).click();
  const d = dialog("Remove shortcut orders?");
  await d.waitFor();
  assert.match(await d.getByRole("button", { name: "Remove" }).getAttribute("class"), /danger/);
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  await sleep(300);
  assert.deepStrictEqual(writes(from), []);
});

for (const [name, change, title, button, danger, text] of [
  ["customers", "ch-add", "Undo change", "Undo", true, /^Remove shortcut customers from LH_Silver\? The data it points at isn't touched\.$/],
  ["prices", "ch-rp", "Undo change", "Undo", true, /^Point prices back at OneLake: Sales Dev \/ LH_Bronze \/ Tables\/dbo\/customers\?$/],
  ["old_orders", "ch-rm", "Recreate shortcut", "Recreate", false, /^Recreate shortcut old_orders in LH_Silver, pointing at OneLake: Sales Dev \/ LH_Bronze \/ Tables\/dbo\/orders\?$/],
]) {
  test(`${button} on ${name}'s change asks first; Cancel sends nothing, and ${button} sends that change`, { skip }, async () => {
    await openShortcuts("changes");
    await changeItems(4);
    const from = app.calls.length;
    await changeRow(name).getByRole("button", { name: button }).click();
    const d = dialog(title);
    await d.waitFor();
    assert.match((await d.locator(".text").innerText()).trim(), text);
    const ok = d.getByRole("button", { name: button, exact: true });
    if (danger) assert.match(await ok.getAttribute("class"), /danger/, "it removes a shortcut from OneLake: a danger confirm");
    await d.getByRole("button", { name: "Cancel" }).click();
    await d.waitFor({ state: "detached" });
    await sleep(300);
    assert.deepStrictEqual(writes(from), []);
    await changeRow(name).getByRole("button", { name: button }).click();
    await ok.click();
    await d.waitFor({ state: "detached" });
    assert.deepStrictEqual(writes(from), [{ method: "shortcut_undo", params: { change_id: change } }]);
    assert.match(await toastText(), button === "Recreate" ? /^Recreated old_orders/ : new RegExp(`^Undid the change to ${name}`));
  });
}

test("Check all now sends every shortcut once, and Check now in a panel sends only that one", { skip }, async () => {
  await openShortcuts("all");
  await tableRows(4);
  const item = (s) => ({ id: s.id, lakehouse_id: s.lakehouse_id, host_workspace_id: s.host_workspace_id, path: s.path, full_path: s.full_path, target_type: s.target_type });
  let from = app.calls.length;
  await page().locator(".monitor-band").getByRole("button", { name: "Check all now" }).click();
  assert.match(await toastText(), /^Checked 4 shortcuts/);
  assert.deepStrictEqual(writes(from), [{ method: "shortcut_check", params: { items: freshRows().map(item) } }]);
  // orders came back healthy.
  const rows = await page().waitForFunction(() => {
    const trs = [...document.querySelectorAll(".sc-table tbody tr")].map((tr) => tr.innerText);
    return !trs.some((t) => /Broken/.test(t)) && trs;
  });
  assert.strictEqual((await rows.jsonValue()).length, 4);
  await page().evaluate(() => { document.getElementById("toast").hidden = true; });
  from = app.calls.length;
  await openRow("landing");
  await page().locator(".preview-panel.open").getByRole("button", { name: "Check now" }).click();
  for (const end = Date.now() + 5000; !writes(from).length && Date.now() < end; await sleep(25));
  assert.deepStrictEqual(writes(from), [{ method: "shortcut_check", params: { items: [item(freshRows()[2])] } }]);
});

test("Save rules sends exactly the rules as set", { skip }, async () => {
  await openShortcuts("access");
  const rules = page().locator(".card.pad", { has: page().locator("b", { hasText: "Shortcut rules" }) });
  await rules.getByRole("button", { name: "Save rules" }).waitFor();
  const from = app.calls.length;
  await rules.getByRole("combobox", { name: RULE_TEXT.cross_env }).selectOption("warn");
  await rules.getByRole("combobox", { name: RULE_TEXT.connections }).selectOption("block");
  await rules.getByRole("combobox", { name: RULE_TEXT.who_creates }).selectOption("admins");
  await rules.getByRole("textbox", { name: "Allowed connections" }).fill(" conn-adls,  conn-s3 ,");
  await rules.getByRole("button", { name: "Save rules" }).click();
  assert.match(await toastText(), /^Saved the shortcut rules/);
  assert.deepStrictEqual(writes(from), [{ method: "shortcut_save_rules", params: { rules: { ...RULES, cross_env: "warn", connections: "block",
    who_creates: "admins", allowed_connections: ["conn-adls", "conn-s3"] } } }]);
  // Read back as saved: the rules draw again from what the engine now holds.
  await page().waitForFunction(() => [...document.querySelectorAll(".sc-side input")].some((i) => i.value === "conn-adls, conn-s3"));
  assert.strictEqual(await rules.getByRole("combobox", { name: RULE_TEXT.cross_env }).inputValue(), "warn");
  assert.strictEqual(await rules.getByRole("textbox", { name: "Allowed connections" }).inputValue(), "conn-adls, conn-s3");
});

test("an engine refusal shows a plain message, and the list stays as it was", { skip }, async () => {
  await openShortcuts("all");
  await tableRows(4);
  // Remove, refused by Fabric.
  fail.delete_shortcut = DENIED;
  await openRow("customers");
  await page().locator(".preview-panel.open").getByRole("button", { name: "Remove" }).click();
  await dialog("Remove shortcut customers?").getByRole("button", { name: "Remove" }).click();
  let said = await toastText();
  assert.strictEqual(said.split("\n")[0], `Not removed: ${PERMISSION}`);
  assert.doesNotMatch(said, /HTTPError|403|api\.fabric/);
  assert.deepStrictEqual(names(await tableRows(4)), ["orders", "landing", "prices", "customers"]);
  assert.strictEqual(await page().locator(".preview-panel.open .name").textContent(), "customers", "the shortcut stays open");
  // New shortcut, refused by the engine's own rules check: the dialog keeps what was picked.
  await page().evaluate(() => { document.getElementById("toast").hidden = true; });
  fail.create_shortcut = "PermissionError: The platform's shortcut rules stop this: Only platform admins create shortcuts here. Send a request instead: Shortcuts > Requests";
  await page().getByRole("button", { name: "New shortcut" }).click();
  const d = dialog("New shortcut");
  await fillDialog(d, { host: "lh-silver", where: "Tables/dbo", target: "lh-bronze", table: "Tables/dbo/orders", name: "bronze_orders" });
  await d.getByRole("button", { name: "Create shortcut" }).click();
  said = await toastText();
  assert.strictEqual(said.split("\n")[0], "Shortcut not created: The platform's shortcut rules stop this: Only platform admins create shortcuts here. Send a request instead: Shortcuts > Requests");
  assert.doesNotMatch(said, /PermissionError|This PC/);
  assert.strictEqual(await d.getByRole("textbox", { name: "Name" }).inputValue(), "bronze_orders");
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  assert.strictEqual((await tableRows(4)).length, 4);
  // Undo of a change that's gone from the history.
  await page().evaluate(() => { document.getElementById("toast").hidden = true; });
  fail.shortcut_undo = "LookupError: That change isn't in the history any more";
  await openShortcuts("changes");
  await changeItems(4);
  await changeRow("customers").getByRole("button", { name: "Undo" }).click();
  await dialog("Undo change").getByRole("button", { name: "Undo", exact: true }).click();
  said = await toastText();
  assert.strictEqual(said.split("\n")[0], "Not undone: That change isn't in the history any more");
  assert.strictEqual((await changeItems(4)).length, 4);
});

test("opening each view, a shortcut's panel and its tabs sends no write", { skip }, async () => {
  const from = app.calls.length;
  await openShortcuts("all");
  await tableRows(4);
  await openRow("customers");
  const panel = page().locator(".preview-panel.open");
  await panel.getByRole("tab", { name: "Who can read" }).click();
  await panel.locator("table").waitFor();
  assert.match(await panel.innerText(), /1 of 2 see it empty[\s\S]*Bob[\s\S]*No access/);
  await panel.getByRole("tab", { name: "History" }).click();
  await panel.locator(".sc-change").first().waitFor();
  for (const label of ["Health", "Access", "Changes", "Requests"]) {
    await view(label).click();
    await page().waitForSelector(".sc-body .sc-pad");
  }
  await view("Access").click();
  await page().locator(".card.pad", { has: page().locator("b", { hasText: "Where readers and targets don't match" }) }).locator("table").waitFor();
  await page().getByRole("button", { name: "Save rules" }).waitFor();
  await view("Changes").click();
  await changeItems(4);
  await sleep(300);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of READS) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
  assert.deepStrictEqual([...new Set(made.filter((m) => !READS.includes(m) && m !== "catalog" && !baseline.has(m)))], []);
});

test("axe finds nothing serious on each view and dialog", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  const check = async (where, selector) => assert.deepStrictEqual(await axeFindings(page(), selector), [], where);
  await openShortcuts("all");
  await tableRows(4);
  await check("All", ".page.onelake");
  await openRow("customers");
  await page().locator(".preview-panel.open").waitFor();
  await check("All with a shortcut open", ".page.onelake");
  await openShortcuts("health");
  await check("Health", ".page.onelake");
  await openShortcuts("access");
  await page().getByRole("button", { name: "Save rules" }).waitFor();
  await page().locator(".card.pad", { has: page().locator("b", { hasText: "Where readers and targets don't match" }) }).locator("table").waitFor();
  await check("Access", ".page.onelake");
  await openShortcuts("changes");
  await changeItems(4);
  await check("Changes", ".page.onelake");
  await openShortcuts("requests");
  await check("Requests", ".page.onelake");
  await page().getByRole("button", { name: "Request a shortcut" }).click();
  await dialog("Request a shortcut").getByRole("combobox", { name: "Table" }).locator("option[value='Tables/dbo/customers']").waitFor({ state: "attached" });
  await check("Request a shortcut", "#dialog-root .dialog");
  await page().evaluate(() => closeDialog());
  await openShortcuts("all");
  await page().getByRole("button", { name: "New shortcut" }).click();
  await dialog("New shortcut").getByRole("combobox", { name: "Table" }).locator("option[value='Tables/dbo/customers']").waitFor({ state: "attached" });
  await check("New shortcut", "#dialog-root .dialog");
  await page().evaluate(() => closeDialog());
  await openRow("customers");
  await page().locator(".preview-panel.open").getByRole("button", { name: "Remove" }).click();
  await dialog("Remove shortcut customers?").waitFor();
  await check("Remove confirm", "#dialog-root .dialog");
  await page().evaluate(() => closeDialog());
  await openShortcuts("changes");
  await changeItems(4);
  await changeRow("customers").getByRole("button", { name: "Undo" }).click();
  await dialog("Undo change").waitFor();
  await check("Undo confirm", "#dialog-root .dialog");
  await page().evaluate(() => closeDialog());
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

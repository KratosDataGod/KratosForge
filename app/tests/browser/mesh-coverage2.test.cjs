// Data mesh and platform setup (mesh.js), the paths mesh-coverage and mesh-platform-keys leave out:
// This project with the platform in Fabric, a plain Save, someone else's wait to join a domain and its
// link to Requests; Finish or repair for the Keys workspace and a Platform workspace when steps don't
// finish; Join existing for keys that are already shared; and Move keys in when every environment
// already shares them, when there's nothing to copy, when no key changed number, and when keys changed
// during the move. Every engine call is mocked; what the person sees and what was sent is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ME = "chris@contoso.com";

function overview() {
  return {
    project: { name: "orders", domain: "sales", subdomain: "", team: "Orders team", description: "", upgraded: true },
    domains: [{ id: "sales", name: "Sales" }, { id: "finance", name: "Finance" }],
    products: [], published: [], links: [], layers: [], designations: [], projects: [{ name: "orders", domain_id: "sales" }],
    environments: { dev: {}, test: {}, prod: {} },
    platform: { environments: { prod: { kind: "fabric", workspace_name: "Platform Prod" } } }, problems: [], errors: [],
  };
}
const STEPS_FAILED = [{ step: "Keys workspace", status: "done", detail: "Kept Keys" }, { step: "SQL_Keys", status: "error", detail: "The capacity is paused" }];

const db = {};
let app;
const page = () => app.page;
const view = () => page().locator("#view");
const callsOf = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const dialog = (name) => page().getByRole("dialog", { name });
// Waits for a toast with this text; read from the spy, since a redraw behind it may toast over it.
const toast = (re) => page().waitForFunction((src) => window.__toasts.some((t) => new RegExp(src).test(t)), re.source);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    meta_project: () => ({ path: "C:/work/orders", name: "Orders", version: "3", templates: {}, layers: [], products: [], links: [], environments: {},
      exceptions: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [], project: { name: "orders", domain: "sales" }, entities: [] }),
    meta_runs: () => [],
    mesh_overview: () => db.overview,
    mesh_members: () => db.members,
    mesh_workspace_domains: () => ({ workspaces: [], suggested: null, unnamed: [], imported: false }),
    mesh_set_project: () => ({ project: { name: "orders" } }),
    list_capacities: () => [{ id: "cap-1", displayName: "F8 West", sku: "F8" }],
    list_workspaces: () => [{ id: "ws-keys", displayName: "Keys (central)" }],
    platform_keys_setup: () => ({ ready: false, steps: STEPS_FAILED }),
    platform_setup: () => ({ ready: false, steps: [{ step: "Platform workspace", status: "done", detail: "Kept Platform Prod" },
      { step: "SQL_Platform", status: "error", detail: "Not allowed on a trial capacity" }] }),
    platform_keys_join: () => ({ keys: { shared: true } }),
    platform_share_keys: (p) => (p.approve ? db.shared : db.plan),
  } });
  await page().evaluate((me) => {
    state.user = me;
    // A join redraws the page behind it, whose own reads may toast too: each toast is kept to read back.
    window.__toasts = [];
    const shown = toast;
    window.toast = (text, ...rest) => { window.__toasts.push(text); return shown(text, ...rest); };
  }, ME);
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(db, {
    overview: overview(),
    members: { project: "orders", members: [{ user: ME, role: "owner" }], earlier: false, can_change: true, domain_request: null },
    plan: { already: true }, shared: null,
  });
  await page().evaluate(() => {
    closeDialog(); closeDialog(); state.workspaces = null; window.__toasts = [];
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
  });
  app.calls.length = 0;
});

async function workspacesTab(ready) {
  await page().evaluate(() => { go("home"); goMeta("workspaces"); });
  await view().locator(ready).first().waitFor();
}

test("This project with the platform in Fabric names it and offers no setup link; a plain Save says where it went", { skip }, async () => {
  await workspacesTab("text=Project orders");
  const note = view().getByText(/^Domains, glossary and published products are kept in/);
  assert.strictEqual(await note.textContent(), "Domains, glossary and published products are kept in the platform in Platform Prod. ");
  assert.strictEqual(await view().getByRole("link", { name: "Set up the platform in Fabric" }).count(), 0);
  await view().getByRole("button", { name: "Save" }).click();
  await toast(/^Saved to project\.toml$/);
  assert.deepStrictEqual(callsOf("mesh_set_project"), [{ fields: { domain: "sales", subdomain: "", team: "Orders team", description: "" } }]);
  assert.deepStrictEqual(app.errors, []);
});

test("someone else's wait to join a domain names who decides, offers no Withdraw, and links to Requests", { skip }, async () => {
  db.members = { ...db.members, domain_request: { id: "r7", mine: false, requested_by: "olu@contoso.com", requested_at: new Date().toISOString(),
    detail: { domain: "finance" } } };
  await workspacesTab(".mesh-join");
  const card = view().locator(".mesh-join");
  assert.match(await card.innerText(), /Waiting on Finance's owners/);
  assert.match(await card.innerText(), /olu@contoso\.com asked .* for orders to join Finance\. It has no owners, so a platform admin decides it on Data mesh > Requests\. Until then the project stays in its current domain\./);
  assert.strictEqual(await card.getByRole("button", { name: "Withdraw" }).count(), 0);
  await card.getByRole("link", { name: "Data mesh > Requests" }).click();
  await page().waitForFunction(() => state.view === "metadata" && state.meta.tab === "requests");
  assert.deepStrictEqual(app.errors, []);
});

test("Finish or repair for the Keys workspace keeps its name and says which steps didn't finish", { skip }, async () => {
  await page().evaluate(() => keysSetupDialog({ keys: { workspace_id: "ws-keys", workspace_name: "Keys" } }));
  const d = dialog("Finish the Keys workspace");
  await d.locator("option", { hasText: "F8 West (F8)" }).waitFor({ state: "attached" });
  assert.ok(await d.locator("label", { hasText: "Workspace" }).locator("input").isDisabled(), "a workspace already made keeps its name");
  await d.getByRole("button", { name: /^Finish setup/ }).click();
  await d.getByText("Some steps didn't finish. Fix what they say, then choose Finish or repair.").waitFor();
  assert.deepStrictEqual(await d.locator(".steps-list li").allInnerTexts().then((x) => x.map((s) => s.trim())), ["Keys workspace: Kept Keys", "SQL_Keys: The capacity is paused"]);
  assert.deepStrictEqual(callsOf("platform_keys_setup"), [{ name: "Keys", capacity_id: "cap-1" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Finish or repair for Prod's platform says which steps didn't finish and toasts nothing", { skip }, async () => {
  await page().evaluate(() => platformSetupDialog("prod", { default_names: { prod: "KratosForge Platform" },
    environments: { prod: { kind: "fabric", workspace_id: "ws-p", workspace_name: "Platform Prod" } } }));
  const d = dialog("Finish the Prod platform");
  await d.locator("option", { hasText: "F8 West (F8)" }).waitFor({ state: "attached" });
  await d.getByRole("button", { name: /^Finish setup/ }).click();
  await d.getByText("Some steps didn't finish. Fix what they say, then choose Finish or repair.").waitFor();
  assert.match(await d.locator(".steps-list").innerText(), /SQL_Platform: Not allowed on a trial capacity/);
  assert.deepStrictEqual(callsOf("platform_setup"), [{ env: "prod", name: "Platform Prod", capacity_id: "cap-1" }]);
  assert.deepStrictEqual(await page().evaluate(() => window.__toasts), []);
});

test("Join existing for keys that every environment already shares says so", { skip }, async () => {
  await page().evaluate(() => { keysJoin(); });
  const d = dialog("Join the Keys workspace");
  await d.locator("select").selectOption("ws-keys");
  await d.getByRole("button", { name: "Join" }).click();
  await toast(/^Every environment now uses Keys \(central\)$/);
  assert.deepStrictEqual(callsOf("platform_keys_join"), [{ workspace_id: "ws-keys" }]);
});

test("Move keys in: already shared, nothing to copy, and a move where no key changed number", { skip }, async () => {
  await page().evaluate(() => { moveKeysIn(); });
  await toast(/^Every environment already uses the one keys database$/);
  assert.strictEqual(await page().locator(".dialog").count(), 0);
  assert.deepStrictEqual(callsOf("platform_share_keys"), [{}]);

  db.plan = { tables: [], renumbered: 0, message: "Every environment's keys close while they move. Confirm to continue." };
  db.shared = { ok: true, renumbered: 0, tables: [] };
  await page().evaluate(() => { moveKeysIn(); });
  const d = dialog("Move keys in");
  await d.getByText("No key tables yet, so nothing is copied.").waitFor();
  assert.strictEqual(await d.locator(".text").first().textContent(), "Every environment's keys close while they move.");
  assert.strictEqual(await d.getByText(/keys change number/).count(), 0);
  await d.getByRole("button", { name: "Move keys in" }).click();
  const done = dialog("Keys moved");
  await done.getByText("Every environment now uses the one keys database. No key changed number.").waitFor();
  assert.deepStrictEqual(callsOf("platform_share_keys"), [{}, {}, { approve: true }]);
  await toast(/^Every environment now uses the one keys database$/);
  assert.deepStrictEqual(app.errors, []);
});

test("Move keys in that didn't switch names the keys that changed during the move and the tables that didn't check out", { skip }, async () => {
  db.plan = { tables: [{ env: "prod", table: "customer", keys: 1200, renumbered: 0 }], renumbered: 0, message: "Keys close while they move." };
  db.shared = { ok: false, message: "Nothing switched.", changed_during_move: ["customer"],
    tables: [{ env: "prod", table: "customer", match: true }, { env: "test", table: "customer", match: false }] };
  await page().evaluate(() => { moveKeysIn(); });
  const plan = dialog("Move keys in");
  assert.match(await plan.locator("tbody tr").innerText(), /Prod\s+sk\.customer\s+1,200\s+0 \(kept\)/);
  await plan.getByRole("button", { name: "Move keys in" }).click();
  const d = dialog("Keys didn't switch");
  await d.getByText("Nothing switched.").waitFor();
  assert.deepStrictEqual(await d.locator("li").allInnerTexts(), ["customer changed during the move", "Test customer: didn't check out"]);
  assert.deepStrictEqual(await d.locator(".dialog-actions button").allInnerTexts(), ["Cancel the move", "Move again"]);
  await d.getByRole("button", { name: "Move again" }).click();
  await dialog("Move keys in").waitFor();
  assert.deepStrictEqual(callsOf("platform_share_keys"), [{}, { approve: true }, {}]);
  assert.deepStrictEqual(app.errors, []);
});

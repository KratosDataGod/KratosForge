// Platform (platform.js), the paths the other platform, product and landing tests leave out: Overview
// with problems, teams and late sources; owner coverage without platform_presence; the landing zone
// with one landing workspace and nothing in it (a problem, Add source, Remove zone), and per
// environment under the organization's policy, with an unregistered source and a move that didn't
// finish; Data mesh > Products' Link, Request access, Requested and Deprecated, and a search that
// matches nothing; a product's page: Follow refused, Scan now (with items the scan didn't report, and
// refused), access given in a domain, its review (all still needed, refused), taking access away
// (with a review, and refused), a promise whose incidents can't be read or that is broken, and Add a
// promise for a product the open project doesn't have. Every engine call is mocked; what the person
// sees and what was sent is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ME = "ana@contoso.com";
const hoursAgo = (n) => new Date(Date.now() - n * 36e5).toISOString();
const PC = { kind: "pc" };

const base = { project: "finance", description: "", owner: "olu@contoso.com", access: "public", status: "published", version: "1", domain_id: "finance",
  ports: [{ port: "entries", entity: "silver.entries", env: "prod" }], consumers: [], granted: [], can_link: false, why: "", linked: false,
  following: false, promise: null, items: [], health: {} };
const PRODUCTS = () => [
  { ...base, id: "finance/ledger", name: "ledger", can_link: true },
  { ...base, id: "ops/stores", project: "ops", name: "stores", status: "deprecated", deprecated_on: "2027-01-01", owner: "", why: "request", domain_id: "ops" },
  { ...base, id: "hr/people", project: "hr", name: "people", why: "requested", domain_id: "hr" },
  { ...base, id: "web/clicks", project: "web", name: "clicks", linked: true, domain_id: "" },
  { ...base, id: "crm/orders", project: "crm", name: "orders", owner: `${ME}; bo@contoso.com`, access: "domain", domain_id: "sales",
    ports: [{ port: "orders", entity: "silver.orders", env: "prod" }], granted: ["billing", "ledger"], consumers: [{ consumer_project: "billing" }] },
];
const TRUST = () => ({ product_id: "crm/orders", admin: true, items: [
  { item_type: "Lakehouse", workspace_id: "w1", item_id: "i1", item_name: "LH_Orders", env: "prod", endorsement: "Certified", certified_by: "Olu", scanned_at: hoursAgo(5) }] });
const REVIEWS = () => ({ due: 1, every_days: 90, grants: [
  { product_id: "crm/orders", project: "billing", due: true, granted_at: hoursAgo(24 * 120) },
  { product_id: "crm/orders", project: "ledger", due: false, reviewed_at: hoursAgo(24 * 3) }] });

const LOCAL = (more = {}) => ({ central: false, kind: "local", where: "C:/platform/landing", landing: {}, zones: [], policy: false,
  default_landing_name: "Platform - Landing", problem: null, system_types: [], sources: [], ...more });

const db = {};
function reset() {
  Object.assign(db, {
    me: { user: ME, admin: true, can_claim: false, domains_owned: [] },
    overview: null, coverage: { available: false, problem: "Not set up" },
    products: PRODUCTS(), reviews: REVIEWS(), trust: TRUST(), scan: null, follow: null, confirm: null, revoke: null, incidents: null,
    landing: LOCAL(), landingSetup: null, mesh: null, canStatus: false, requests: [],
    domains: [{ id: "sales", name: "Sales" }, { id: "ops", name: "Ops", fabric_domain: "f-ops" }],
  });
}
reset();
const answer = (x, p) => { if (x instanceof Error) throw x; return typeof x === "function" ? x(p) : structuredClone(x); };

let app;
const page = () => app.page;
const view = () => page().locator("#view");
const sent = (m) => app.calls.filter((c) => c.method === m).map((c) => JSON.parse(JSON.stringify(c.params)));
const dialog = (name) => page().getByRole("dialog", { name });
// Waits for a toast with this text; read from a spy, since a redraw may toast over it.
const toast = (re) => page().waitForFunction((src) => window.__toasts.some((t) => new RegExp(src).test(t)), re.source);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    platform_whoami: () => structuredClone(db.me),
    platform_note_seen: () => ({}),
    platform_overview: (p) => ({ env: p.env, ...structuredClone(db.overview) }),
    platform_owner_coverage: () => answer(db.coverage),
    platform_audit: () => [],
    platform_products: () => ({ project: { name: "crm" }, products: structuredClone(db.products), can_follow: true, can_status: db.canStatus }),
    platform_access_reviews: () => structuredClone(db.reviews),
    platform_product_trust: () => structuredClone(db.trust),
    platform_scan_endorsement: () => answer(db.scan),
    platform_follow: () => answer(db.follow),
    platform_confirm_access: (p) => answer(db.confirm, p),
    platform_revoke: (p) => answer(db.revoke, p),
    incidents_list: () => answer(db.incidents),
    platform_request: (p) => ({ id: "r1", target: p.target }),
    mesh_overview: () => structuredClone(db.mesh),
    meta_project: () => ({ path: "C:/work/crm", name: "crm", version: "1", templates: {}, layers: [], products: [], links: [], environments: {},
      exceptions: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [], project: { name: "crm" }, entities: [] }),
    platform_landing: (p) => ({ env: p.env, ...structuredClone(db.landing) }),
    platform_landing_files: () => [],
    platform_delete_landing_zone: () => ({ ok: true }),
    platform_landing_setup: () => answer(db.landingSetup),
    list_capacities: () => [{ id: "cap1", displayName: "F64", sku: "F64" }],
    platform_domains: () => structuredClone(db.domains),
    mesh_map: () => ({ domains: [], projects: [], products: [] }),
    platform_delete_domain: () => ({ ok: true }),
    platform_glossary: () => [],
    platform_requests: () => structuredClone(db.requests),
    governance_overview: () => ({ policies: [{ id: "naming", name: "Naming", current_version: null, status: "draft" }], domains: [], projects: [] }),
    fabric_connections: () => ({ error: "HTTPError: 401", connections: [] }),
  } });
  await page().evaluate(() => {
    window.__toasts = [];
    const shown = toast;
    window.toast = (text, ...rest) => { window.__toasts.push(text); return shown(text, ...rest); };
  });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  reset();
  await page().evaluate((me) => {
    closeDialog(); closeDialog(); window.__toasts = []; state.user = me;
    Object.assign(platformPage, { env: "dev", landingCentral: false, keysShared: false, source: null, me: null });
    Object.assign(productsPage, { open: null, following: false, status: false, reviewDue: false, query: "", domain: "all", offer: "all" });
    go("home");
  }, ME);
  app.calls.length = 0;
  app.errors.length = 0;
});

const OVERVIEW = () => ({
  counts: { domains: 2, subdomains: 1, projects: 3, teams: 2, key_tables: 1, keys: 1200, products: 4, published: 3, requests: 0 },
  landing: { problem: "LH_Landing in Prod can't be read", sources: [
    { name: "erp", late: true, newest: hoursAgo(30), readers: [{ project: "crm" }, { project: "billing" }] },
    { name: "shop", late: true, newest: "", readers: [] },
    { name: "web", late: false, newest: "", readers: [{ project: "web" }] }] },
  problems: ["SQL_Platform in Prod isn't reachable"], me: { admin: true }, needs_you: [], mine: [],
  status: { source: "settings", default_names: {}, environments: { dev: PC, test: PC, prod: PC } }, recent: [],
});

test("Overview lists what it couldn't read, teams joined, late sources and who reads each", { skip }, async () => {
  db.overview = OVERVIEW();
  await page().evaluate(() => goPlatform("overview"));
  await view().locator(".problems").waitFor();
  assert.deepStrictEqual(await view().locator(".problems li").allInnerTexts(), ["SQL_Platform in Prod isn't reachable", "LH_Landing in Prod can't be read"]);
  assert.match(await view().locator(".plat-stat", { hasText: "Projects joined" }).innerText(), /2 teams, joined to a domain/);
  const card = view().locator(".card", { hasText: "Landing zone" });
  assert.strictEqual(await card.locator(".fail-text").innerText(), "erp, shop are late.");
  const rows = await card.locator("table.list tbody tr").allInnerTexts();
  assert.match(rows[0], /^erp\s+late, .+\s+2 projects$/);
  assert.match(rows[1], /^shop\s+nothing yet\s+none$/);
  assert.match(rows[2], /^web\s+nothing yet\s+1 project$/);
  assert.deepStrictEqual(sent("platform_overview"), [{ env: "dev" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Owners without platform_presence says quiet owners aren't checked, and with no gaps says all have two", { skip }, async () => {
  db.overview = { ...OVERVIEW(), problems: [], landing: { sources: [] } };
  db.coverage = { available: true, presence: false, covered: 2, checked: 3, quiet_days: 90, gaps: [
    { kind: "source", id: "erp", name: "erp", owners: ["ERP team"], unchecked: ["ERP team"], problems: ["No second owner"], opens: "landing" }] };
  await page().evaluate(() => goPlatform("overview"));
  const card = view().locator(".owner-coverage");
  await card.locator("table").waitFor();
  assert.match(await card.innerText(), /2 of 3 covered/);
  assert.match(await card.innerText(), /Whether owners have gone quiet isn't checked: your organization hasn't turned on platform_presence\./);
  assert.match(await card.locator('tr[data-owned="source:erp"]').innerText(), /Source\s+erp\s+ERP team\s+Names, not sign-ins, can't be checked for being quiet\s+No second owner/);
  await card.getByRole("button", { name: "Open" }).click();
  await page().waitForFunction(() => state.view === "platform" && platformPage.tab === "landing");

  db.coverage = { available: true, presence: false, covered: 3, checked: 3, gaps: [] };
  await page().evaluate(() => goPlatform("overview"));
  await view().locator(".owner-coverage").getByText("Every domain, data product and source has two owners.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

const CENTRAL = () => ({ central: true, kind: "fabric", where: "Platform - Landing", landing: { workspace_id: "ws-l", workspace_name: "Platform - Landing" },
  policy: false, default_landing_name: "Platform - Landing", problem: "The Shared zone's lakehouse can't be listed", system_types: ["SAP"], sources: [],
  zones: [
    { id: "shared", name: "Shared", lakehouse_name: "LH_Landing", secure: false, readers: [], label: "", devtest: "read", domain_id: null, switched_at: "2026-10-01T00:00:00Z" },
    { id: "hr", name: "HR", lakehouse_name: "LH_Landing_HR", secure: false, readers: ["HR Engineers"], label: "", devtest: "none", domain_id: "hr" }] });

test("one landing workspace with no sources: its problem, Add source with its zones, and Remove zone behind a danger confirm", { skip }, async () => {
  db.landing = CENTRAL();
  await page().evaluate(() => goPlatform("landing"));
  await view().getByText("No sources yet").waitFor();
  assert.strictEqual(await view().locator(".fail-text").first().innerText(), "The Shared zone's lakehouse can't be listed");
  assert.match(await view().locator(".zone-card", { hasText: "HR" }).innerText(), /0 sources\. Prod reads it\. Dev and Test don't\./);
  assert.strictEqual(await view().locator(".zone-card", { hasText: "Shared" }).getByRole("button", { name: "Remove zone" }).count(), 0, "Shared stays");

  await view().getByRole("button", { name: "Add source" }).click();
  const add = dialog("Add a source");
  assert.deepStrictEqual(await add.locator("label", { hasText: "Zone" }).locator("option").allInnerTexts(), ["Shared", "HR"]);
  await add.getByRole("button", { name: "Cancel" }).click();

  await view().locator(".zone-card", { hasText: "HR" }).getByRole("button", { name: "Remove zone" }).click();
  const confirm = dialog("Remove the zone HR?");
  assert.match(await confirm.innerText(), /Only the catalog's record goes\. LH_Landing_HR and its files stay in Platform - Landing/);
  assert.strictEqual(await confirm.locator("button.danger-solid").innerText(), "Remove zone");
  await confirm.locator("button.danger-solid").click();
  await page().waitForFunction(() => document.querySelectorAll("[role=dialog]").length === 0);
  await view().getByText("No sources yet").waitFor();
  assert.deepStrictEqual(sent("platform_delete_landing_zone"), [{ id: "hr" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("per environment: an empty landing zone, a source not registered yet, and the organization's policy", { skip }, async () => {
  db.landing = LOCAL();
  await page().evaluate(() => goPlatform("landing"));
  await view().getByText("The landing zone is empty").waitFor();
  assert.strictEqual(await view().getByRole("button", { name: "Move to one landing workspace" }).count(), 1);

  db.landing = LOCAL({ policy: true, problem: "LH_Landing can't be read", sources: [{ name: "shop", registered: false, readers: [], entities: [], newest: "", late: false }] });
  await page().evaluate(() => render());
  await view().getByText("Your organization's policy sets the platform, so one landing workspace is added to that policy.").waitFor();
  assert.strictEqual(await view().getByRole("button", { name: "Move to one landing workspace" }).count(), 0);
  assert.match(await view().locator("table.list tbody tr").innerText(), /^shop\s*not registered\s+0\s+nothing yet\s+not set\s+none$/);
  assert.strictEqual(await view().locator(".card .fail-text").first().innerText(), "LH_Landing can't be read");
  assert.strictEqual(await view().locator(".side-panel").getByRole("button", { name: "Register" }).count(), 1);
  assert.deepStrictEqual(app.errors, []);
});

test("the move to one landing workspace says when nothing was set up and when steps didn't finish", { skip }, async () => {
  db.landing = LOCAL();
  db.landingSetup = new Error("PlatformError: Only platform admins can set up the landing workspace");
  await page().evaluate(() => goPlatform("landing"));
  const start = async () => {
    await view().getByRole("button", { name: "Move to one landing workspace" }).click();
    const d = dialog("Move to one landing workspace");
    await d.locator("option", { hasText: "F64 (F64)" }).waitFor({ state: "attached" });
    await d.getByRole("button", { name: "Move" }).click();
    await dialog("Create Platform - Landing?").getByRole("button", { name: "Create" }).click();
    return d;
  };
  let d = await start();
  await d.getByText("Nothing was set up.").waitFor();
  assert.deepStrictEqual(sent("platform_landing_setup"), [{ workspace_name: "Platform - Landing", capacity_id: "cap1" }]);
  await d.locator(".dialog-actions").getByRole("button", { name: "Close" }).click();

  db.landingSetup = { ready: false, steps: [{ step: "Landing workspace", status: "done", detail: "Kept Platform - Landing" },
    { step: "LH_Landing", status: "error", detail: "The capacity is paused" }] };
  d = await start();
  await d.getByText("Some steps didn't finish, so runs still read each environment's LH_Landing. Fix what they say, then move again.").waitFor();
  assert.match(await d.locator(".steps-list").innerText(), /LH_Landing: The capacity is paused/);
  assert.ok(!(await page().evaluate(() => window.__toasts)).includes("One landing workspace is set up"));
  await d.locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
});

async function productsList() {
  await page().evaluate(() => { catalogPage.tab = "products"; go("onelake"); render(); });
  await view().locator("table.list tbody tr").first().waitFor();
}
// A product's row, by its id under its name.
const productRow = (id) => view().locator("table.list tbody tr").filter({ has: page().locator(".mono", { hasText: new RegExp(`^${id}$`) }) });

test("Products: Link, Request access, Requested, Linked and Deprecated, and a search that matches nothing", { skip }, async () => {
  db.mesh = { project: { name: "crm" }, products: [], links: [], domains: [],
    published: [{ id: "finance/ledger", project: "finance", status: "published", ports: [{ port: "entries", env: "prod" }] }] };
  await productsList();
  assert.match(await productRow("ops/stores").innerText(), /Deprecated 2027-01-01/);
  assert.match(await productRow("ops/stores").locator("td").nth(2).innerText(), /^not set$/);
  assert.strictEqual(await productRow("hr/people").locator("td").last().innerText(), "Requested");
  assert.strictEqual(await productRow("web/clicks").locator("td").last().innerText(), "Linked");

  await productRow("finance/ledger").getByRole("button", { name: "Link" }).click();
  const link = dialog("New link");
  assert.strictEqual(await link.locator("label", { hasText: "Name entities use" }).locator("input").inputValue(), "ledger_entries");
  assert.strictEqual(await link.locator("label", { hasText: "Product" }).locator("select").inputValue(), "finance/ledger");
  await link.getByRole("button", { name: "Cancel" }).click();

  await productRow("ops/stores").getByRole("button", { name: "Request access" }).click();
  const ask = dialog("Request access to stores");
  assert.match(await ask.innerText(), /^Its owner decides\. Once approved, Link works for this project\.$/m);
  await ask.locator("textarea").fill("For the store dashboard");
  await ask.getByRole("button", { name: "Send request" }).click();
  await toast(/^Request sent$/);
  assert.deepStrictEqual(sent("platform_request"), [{ kind: "access", target: "ops/stores", reason: "For the store dashboard", detail: {} }]);

  await view().locator(".catalog-search").fill("no such product");
  await view().getByText("Nothing matches.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

async function openOwn() {
  await productsList();
  await productRow("crm/orders").locator("a.product-open").click();
  await view().locator(".product-access table").waitFor();
}

test("a product's page: Follow refused says why; Scan now names what Fabric didn't report, and a refused scan says why", { skip }, async () => {
  db.follow = new Error("PlatformError: Following needs the platform in Fabric");
  db.scan = { workspaces: 2, recorded: 3, not_reported: ["LH_Old"] };
  await openOwn();
  const trust = view().locator(".trust-endorsement");
  await trust.getByText("Certified by Olu in Fabric.").waitFor();
  await view().getByRole("button", { name: "Follow" }).click();
  await toast(/^Following needs the platform in Fabric$/);
  assert.strictEqual(await view().locator(".follow-button").innerText(), "Follow");
  assert.strictEqual(await view().locator(".follow-button").isDisabled(), false);
  assert.deepStrictEqual(sent("platform_follow"), [{ product_id: "crm/orders", follow: true }]);

  db.trust.items[0].endorsement = "Promoted";
  const reads = sent("platform_product_trust").length;
  await trust.getByRole("link", { name: "Scan now" }).click();
  await toast(/^Scanned 2 workspaces: 3 items read\. Fabric's scan didn't report LH_Old, so it stays unknown\.$/);
  await trust.getByText("Promoted in Fabric.").waitFor();
  assert.deepStrictEqual(sent("platform_scan_endorsement"), [{ product_id: "crm/orders" }]);
  assert.strictEqual(sent("platform_product_trust").length, reads + 1, "the scan read nothing back, so the page reads it again");

  db.scan = new Error("PermissionError: Only Fabric admins can scan");
  await trust.getByRole("link", { name: "Scan now" }).click();
  await trust.locator(".fail-text", { hasText: "Only Fabric admins can scan" }).waitFor();
  assert.strictEqual(await trust.locator(".trust-scan").innerText(), "Scan now");
  assert.deepStrictEqual(app.errors, []);
});

test("access given in a domain: its review, All still needed, and a confirm the platform refuses", { skip }, async () => {
  await openOwn();
  const card = view().locator(".product-access");
  assert.match(await card.innerText(), /Projects in sales can link to it without being given access\./);
  assert.match(await card.locator('tr[data-project="billing"]').innerText(), /billing\s+Linked\s+Review due\s+Given .+, never confirmed/);
  assert.match(await card.locator('tr[data-project="ledger"]').innerText(), /ledger\s+Not yet\s+Confirmed /);
  assert.match(await card.innerText(), /Confirm every 90 days who still needs access\. Nothing is taken away by itself\./);

  db.confirm = (p) => ({ ...REVIEWS(), grants: REVIEWS().grants.map((g) => ({ ...g, due: false, reviewed_at: new Date().toISOString() })), projects: p.projects });
  await card.getByRole("button", { name: "All still needed" }).click();
  await toast(/^Confirmed all 2 projects still need orders\.$/);
  await card.locator('tr[data-project="billing"]', { hasNotText: "Review due" }).waitFor();
  assert.deepStrictEqual(sent("platform_confirm_access"), [{ product_id: "crm/orders", projects: ["billing", "ledger"] }]);

  db.confirm = new Error("PlatformError: Only the product's owner can confirm access");
  await card.locator('tr[data-project="ledger"]').getByRole("button", { name: "Still needed" }).click();
  await card.locator(".fail-text", { hasText: "Only the product's owner can confirm access" }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("taking access away drops its review too; a refusal says why and keeps the row", { skip }, async () => {
  db.revoke = () => [{ project: "ledger" }];
  await openOwn();
  const card = view().locator(".product-access");
  await card.locator('tr[data-project="billing"]').getByRole("button", { name: "Take access away" }).click();
  const confirm = dialog("Take billing's access to orders away?");
  assert.match(await confirm.innerText(), /billing already links to it: that link stays and its runs keep reading/);
  await confirm.locator("button.danger-solid").click();
  await card.locator('tr[data-project="billing"]').waitFor({ state: "detached" });
  await toast(/^billing no longer has access to orders\.$/);
  assert.deepStrictEqual(sent("platform_revoke"), [{ product_id: "crm/orders", project: "billing" }]);
  // One project left: no All still needed, and its review is still shown.
  assert.strictEqual(await card.getByRole("button", { name: "All still needed" }).count(), 0);
  assert.match(await card.locator('tr[data-project="ledger"]').innerText(), /Confirmed /);

  db.revoke = new Error("PlatformError: The catalog can't be written now");
  await card.locator('tr[data-project="ledger"]').getByRole("button", { name: "Take access away" }).click();
  await dialog("Take ledger's access to orders away?").locator("button.danger-solid").click();
  await card.locator(".fail-text", { hasText: "The catalog can't be written now" }).waitFor();
  assert.strictEqual(await card.locator('tr[data-project="ledger"]').count(), 1);
  assert.deepStrictEqual(app.errors, []);
});

test("its own product's promise: incidents that can't be read say so; a broken one opens its incident", { skip }, async () => {
  db.products = PRODUCTS().map((p) => (p.name === "orders" ? { ...p, promise: { within_hours: 24, envs: ["prod"] } } : p));
  db.incidents = new Error("OSError: the incident store is locked");
  await openOwn();
  const card = view().locator(".product-promise");
  await card.getByText("Couldn't read the incidents. Refresh to try again.").waitFor();
  assert.deepStrictEqual(sent("incidents_list"), [{ env: "prod", include_resolved: false }]);

  db.incidents = { observation: { state: "live" }, incidents: [{ id: "inc-1", kind: "late", state: "open", name: "silver.orders",
    evidence: { promise_products: ["orders"], reasons: ["orders: Loaded 30 h ago"] } }] };
  await page().evaluate(() => { window.__opened = []; window.openIncident = (i) => window.__opened.push(i.id); render(); });
  await card.locator(".pill-s.wrn", { hasText: "Broken" }).waitFor();
  assert.strictEqual(await card.locator(".promise-why").innerText(), "Loaded 30 h ago");
  await card.getByRole("button", { name: "Open the incident" }).click();
  assert.deepStrictEqual(await page().evaluate(() => window.__opened), ["inc-1"]);
  assert.deepStrictEqual(app.errors, []);
});

test("Add a promise for a product the open project doesn't have says its file can't be edited here", { skip }, async () => {
  db.mesh = { project: { name: "crm" }, products: [{ name: "other" }], published: [], links: [], domains: [] };
  await openOwn();
  await view().locator(".product-promise").getByRole("button", { name: "Add a promise" }).click();
  await toast(/^orders isn't in the project open on this PC, so its file can't be edited here\.$/);
  assert.strictEqual(await page().locator("[role=dialog]").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("the environment buttons read Overview again for the one picked", { skip }, async () => {
  db.overview = { ...OVERVIEW(), problems: [], landing: { sources: [] } };
  await page().evaluate(() => goPlatform("overview"));
  await view().locator(".plat-stats").waitFor();
  await view().getByRole("group", { name: "Environment" }).getByRole("button", { name: "Test" }).click();
  await page().waitForFunction(() => document.querySelector('.platform-env button.on')?.textContent === "Test");
  await view().locator(".plat-stat", { hasText: "Business key tables" }).getByText("1,200 keys in Test").waitFor();
  assert.deepStrictEqual(sent("platform_overview"), [{ env: "dev" }, { env: "test" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("the command bar opens the platform's pages", { skip }, async () => {
  db.overview = { ...OVERVIEW(), problems: [], landing: { sources: [] } };
  const pick = async (label, query) => {
    await page().evaluate((q) => openPalette(q), query);
    await page().getByRole("dialog", { name: "Command bar" }).getByRole("option", { name: label, exact: true }).click();
  };
  await pick("Landing zone", "landing zone");
  await page().waitForFunction(() => state.view === "platform" && platformPage.tab === "landing");
  await pick("Business keys", "business keys");
  await page().waitForFunction(() => state.view === "platform" && platformPage.tab === "keys");
  await pick("Platform", "central team");
  await page().waitForFunction(() => state.view === "platform" && platformPage.tab === "overview");
  await pick("Requests", "requests");
  await page().waitForFunction(() => state.view === "metadata" && state.meta.tab === "requests");
  assert.deepStrictEqual(app.errors, []);
});

test("Products: the domain picker narrows the list, and the Status view opens a product", { skip }, async () => {
  db.canStatus = true;
  await productsList();
  await view().getByRole("combobox", { name: "Domain" }).selectOption("ops");
  await page().waitForFunction(() => document.querySelectorAll("#view table.list tbody tr").length === 1);
  assert.match(await view().locator("table.list tbody tr").innerText(), /ops\/stores/);
  await view().getByRole("combobox", { name: "Domain" }).selectOption("all");
  await view().getByRole("button", { name: "Status" }).click();
  const status = view().locator(".product-status");
  await status.locator('tr[data-product="crm/orders"] a.product-open').click();
  await view().locator(".product-access table").waitFor();
  assert.strictEqual(await view().locator("h2.trust-name").innerText(), "orders");
  assert.deepStrictEqual(app.errors, []);
});

test("Data mesh > Domains: Delete asks first and sends the domain; with none, a team is told an admin imports them", { skip }, async () => {
  await page().evaluate(() => { platformPage.me = null; goMeta("domains"); });
  const row = view().locator('tr[data-domain="ops"]');
  await row.getByRole("button", { name: "Delete" }).click();
  const confirm = dialog("Delete Ops?");
  assert.match(await confirm.innerText(), /Only a domain no project or subdomain uses can be deleted\. It follows a domain in Fabric: delete that one in Fabric's admin portal first/);
  db.domains = [{ id: "sales", name: "Sales" }];
  await confirm.locator("button.danger-solid").click();
  await row.waitFor({ state: "detached" });
  assert.deepStrictEqual(sent("platform_delete_domain"), [{ id: "ops" }]);

  db.me = { user: ME, admin: false };
  db.domains = [];
  await page().evaluate(() => { platformPage.me = null; render(); });
  await view().getByText("No domains yet. A platform admin imports them from Fabric.").waitFor();
  assert.strictEqual(await view().getByRole("button", { name: "Delete" }).count(), 0);
  await page().getByRole("button", { name: "Glossary" }).click();
  await page().waitForFunction(() => state.view === "platform" && platformPage.tab === "glossary");
  assert.deepStrictEqual(app.errors, []);
});

test("New request: data from another team switches to a change and needs a product; an exception needs a published policy", { skip }, async () => {
  db.products = PRODUCTS();
  await page().evaluate(() => newRequestDialog(null, "data"));
  const d = dialog("New request");
  await d.getByRole("group", { name: "What kind" }).waitFor();
  await d.getByRole("button", { name: "A change to one", exact: true }).click();
  assert.strictEqual(await d.getByRole("button", { name: "A change to one", exact: true }).getAttribute("aria-pressed"), "true");
  assert.strictEqual(await d.getByRole("button", { name: "A new data product", exact: true }).getAttribute("aria-pressed"), "false");
  const product = d.getByRole("combobox", { name: "Data product" });
  assert.match((await product.locator("option").allInnerTexts()).join("|"), /ledger \(finance\/ledger\)/);
  await d.getByRole("textbox", { name: "What the data is for" }).fill("Daily orders by store");
  await d.getByRole("button", { name: "Send request" }).click();
  await toast(/^Request sent: data finance\/ledger$/);
  assert.deepStrictEqual(sent("platform_request"), [{ kind: "data", target: "finance/ledger", reason: "Daily orders by store", detail: { what: "change", needed_by: "" } }]);

  await page().evaluate(() => newRequestDialog(null, "exception"));
  await d.getByText("No published policy to ask an exception to.").waitFor();
  await d.getByRole("button", { name: "Send request" }).click();
  assert.strictEqual(sent("platform_request").length, 1, "nothing to send");
  await d.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("Request a source says when your connections can't be read", { skip }, async () => {
  await page().evaluate(() => requestDialog("source", "", null));
  const d = dialog("Request a source");
  await d.getByRole("combobox", { name: "Fabric connection" }).locator("option", { hasText: "Couldn't read your connections" }).waitFor({ state: "attached" });
  await d.getByRole("textbox", { name: "Folder name" }).fill("erp");
  await d.getByRole("button", { name: "Send request" }).click();
  assert.strictEqual(sent("platform_request").length, 0, "a Copy job needs a connection");
  await d.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("declining a shortcut or a platform-only domain that approving made says it's still there", { skip }, async () => {
  const at = "2026-11-18T09:00:00Z";
  const req = (id, kind, target, detail) => ({ id, kind, target, detail, status: "waiting", can_decide: true, requested_by: "lee@contoso.com", requested_at: at });
  db.requests = [req("m-sc", "shortcut", "orders", { name: "orders", lakehouse_name: "LH_Sales", made: { shortcut_id: "x" } }),
    req("m-dom", "domain", "emea", { name: "EMEA", made: { domain_id: "emea" } })];
  await page().evaluate(() => { meshRequests.show = "all"; goMeta("requests"); });
  const said = async (id, title) => {
    await view().locator(`tr[data-request="${id}"]`).getByRole("button", { name: "Decline" }).click();
    const d = dialog(title);
    const text = await d.locator(".wrn-text").innerText();
    await d.getByRole("button", { name: "Cancel" }).click();
    await d.waitFor({ state: "detached" });
    return text;
  };
  assert.strictEqual(await said("m-sc", "Decline shortcut orders?"),
    "Declining deletes nothing in Fabric. The shortcut orders in LH_Sales, made by this request, is still in OneLake: remove it on OneLake > Shortcuts if it isn't wanted.");
  assert.strictEqual(await said("m-dom", "Decline domain emea?"),
    "Declining deletes nothing in Fabric. The domain EMEA, made by this request, is still in the platform: delete it on Data mesh > Domains if it isn't wanted.");
  assert.strictEqual(sent("platform_decide").length, 0);
  assert.deepStrictEqual(app.errors, []);
});

// Data mesh screens (mesh.js), the paths the other mesh tests leave out: Products and links (this
// project's products, other teams' products, Publish, Link, Delete), Links (where each points, Remove,
// nothing to link to), This project's workspaces by designation (Pick, Create, the dismiss button),
// Save with a domain that waits for its owners, members that can't be read, an entity's About card,
// keys in the platform's SQL database, Import from Fabric and a domain imported from Fabric.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ME = "chris@contoso.com";

const pub = (id, more = {}) => ({ id, project: id.split("/")[0], name: id.split("/")[1], status: "published", access: "public", owner: "olu@contoso.com",
  description: "", domain_id: "finance", ports: [], consumers: [], ...more });
function overview() {
  return {
    project: { name: "orders", domain: "sales", subdomain: "", team: "Orders team", description: "", upgraded: true },
    domains: [{ id: "sales", name: "Sales" }, { id: "sales-eu", name: "Sales EU", parent_id: "sales" }, { id: "finance", name: "Finance" }],
    products: [
      { name: "c360", title: "Customer 360", ports: [{ name: "customers", entity: "gold.customers" }], access: "public", status: "published", owner: "ana@contoso.com", version: 2 },
      { name: "drafty", title: "Draft one", ports: [], access: "private", status: "draft", owner: "" }],
    published: [
      pub("orders/c360", { ports: [{ port: "customers", env: "prod" }, { port: "customers", env: "test" }], consumers: ["a", "b"] }),
      pub("finance/ledger", { description: "General ledger", access: "domain", ports: [{ port: "entries", env: "prod" }, { port: "accounts", env: "prod" }] }),
      pub("ops/stores", { status: "deprecated", deprecated_on: "2027-01-01", description: "Stores", domain_id: "ops", owner: "bo@contoso.com", ports: [{ port: "stores", env: "prod" }] }),
      pub("ops/wip", { status: "draft", ports: [{ port: "x", env: "prod" }] })],
    links: [{ alias: "ledger_entries", product: "finance/ledger", port: "entries", reads: { dev: "prod", test: "test", prod: "prod" } },
      { alias: "stores", product: "ops/stores", port: "stores", reads: { dev: "prod", test: "prod", prod: "prod" } }],
    layers: [{ name: "bronze", workspace: "ingest" }],
    environments: { dev: { workspace_id: "w-dev", workspace_name: "Orders Dev", workspaces: { ingest: { id: "w-ing", name: "Orders Ingest - Dev" } } },
      test: { workspace_id: "w-test", workspace_name: "Orders Test" }, prod: {} },
    designations: [{ id: "ingest", label: "Ingest" }],
    projects: [{ name: "orders", domain_id: "sales" }],
    platform: { environments: { prod: { kind: "local" } } }, problems: ["stores: ops/stores ends on 2027-01-01"], errors: [],
  };
}

const db = {};
let app;
const page = () => app.page;
const callsOf = (m) => app.calls.filter((c) => c.method === m);
const view = () => page().locator("#view");
const dialog = () => page().locator("#dialog-root .dialog").last();
const toast = async (re) => {
  await page().waitForFunction((src) => { const t = document.getElementById("toast"); return !t.hidden && new RegExp(src).test(t.textContent); }, re.source);
  return page().locator("#toast").textContent();
};
const mount = (src) => page().evaluate((src) => {
  let box = document.getElementById("t-box");
  if (!box) { box = document.createElement("div"); box.id = "t-box"; document.body.append(box); }
  box.replaceChildren(new Function(`return ${src}`)() || "");
}, src);
const box = () => page().locator("#t-box");
const led = () => page().evaluate(() => window.__led);
const waitCall = async (m, n) => {
  for (let i = 0; i < 200 && callsOf(m).length < n; i++) await page().waitForTimeout(25);
  return callsOf(m)[n - 1];
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    meta_project: () => ({ path: "C:/work/orders", name: "Orders", version: "3", templates: {}, layers: [], products: [], links: [], environments: {},
      exceptions: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [], project: { name: "orders", domain: "sales" },
      entities: [{ name: "gold.customers", layer: "gold", link: null }, { name: "silver.ledger", layer: "silver", link: "ledger_entries" }] }),
    meta_runs: () => [],
    mesh_overview: () => db.overview,
    mesh_members: () => { if (db.members instanceof Error) throw db.members; return db.members; },
    mesh_workspace_domains: () => db.fabricDomains,
    mesh_set_project: () => db.setProject,
    mesh_publish: () => ({ published: db.published }),
    mesh_save_link: (p) => ({ link: p.link }),
    mesh_delete_link: () => ({ removed: true }),
    mesh_delete_product: () => ({ removed: true }),
    mesh_resolve_links: () => { if (db.resolveFail) throw new Error("HTTPError: 403 Client Error: Forbidden for url: https://api.fabric.microsoft.com/v1/x"); return db.resolved; },
    meta_set_designation: () => ({ ok: true }),
    meta_create_designation_workspace: () => ({ workspace_id: "w-new" }),
    list_workspaces: () => [{ id: "w-a", displayName: "Orders Serve - Prod" }],
    list_capacities: () => [{ id: "cap-1", displayName: "F8 West", sku: "F8" }],
    mesh_about: () => { if (db.about instanceof Error) throw db.about; return structuredClone(db.about); },
    mesh_save_about: (p) => ({ ...structuredClone(db.about), description: p.fields.description ?? db.about.description }),
    meta_keys_to_lakehouse: () => ({ ok: true }),
    meta_keys_to_platform: () => db.keysMove,
    platform_import_fabric_domains: () => db.imported,
  } });
  await page().evaluate((me) => {
    state.user = me;
    window.__led = [];
    window.__toasts = [];
    // Import from Fabric shows several toasts in a row; each is kept to read back.
    const shown = toast;
    window.toast = (text, ...rest) => { window.__toasts.push(text); return shown(text, ...rest); };
  }, ME);
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(db, {
    overview: overview(), published: ["orders/c360"], setProject: { project: { name: "orders" } },
    members: { project: "orders", members: [{ user: ME, role: "owner" }], earlier: false, can_change: true, domain_request: null },
    fabricDomains: { workspaces: [], suggested: null, unnamed: [], imported: false },
    resolved: [], resolveFail: false,
  });
  await page().evaluate(() => {
    closeDialog(); window.__led = []; window.__toasts = []; state.workspaces = null;
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    document.getElementById("t-box")?.replaceChildren();
  });
});

async function openTab(tab, ready) {
  await page().evaluate((tab) => { go("home"); goMeta(tab); }, tab);
  await view().locator(ready).first().waitFor();
}

test("Data products lists this project's products and other teams', with what is linked and what ends", { skip }, async () => {
  await openTab("products", "text=This project's data products");
  assert.match(await view().locator(".problems").textContent(), /Fix these first.*stores: ops\/stores ends on 2027-01-01/);
  const mine = await view().locator(".card", { hasText: "This project's data products" }).locator("tbody tr").allTextContents();
  assert.match(mine[0], /Customer 360c360customers ← gold\.customersEveryonePublishedProd, Test2ana@contoso\.com/);
  assert.match(mine[1], /Draft onedraftyThis projectDraftnot yet0not set/);
  const others = view().locator(".card", { hasText: "Other teams' products" });
  const rows = await others.locator("tbody tr").allTextContents();
  assert.strictEqual(rows.length, 3, "every other team's product, drafts too");
  assert.match(rows[0], /finance\/ledgerfinanceGeneral ledgerentrieslinkedaccountsLinkMy domainolu@contoso\.com/);
  assert.match(rows[1], /ops\/storesEnds 2027-01-01/);
  assert.match(await others.textContent(), /kept in the platform on this PC/);
  await page().evaluate(() => { window.__realGoPlatform = goPlatform; window.goPlatform = (t) => window.__led.push(["goPlatform", t]); });
  await others.getByRole("link", { name: "Set up the platform in Fabric" }).click();
  await page().evaluate(() => { window.goPlatform = window.__realGoPlatform; });
  assert.deepStrictEqual(await led(), [["goPlatform", "settings"]]);
});

test("Publish sends the environment and says what was published, or that every product is a draft", { skip }, async () => {
  await openTab("products", "text=This project's data products");
  await view().getByRole("button", { name: /^Publish to the catalog \(Dev\)/ }).click();
  await toast(/^Published orders\/c360 in Dev$/);
  assert.deepStrictEqual(callsOf("mesh_publish").at(-1).params, { env: "dev" });
  db.published = [];
  await view().locator("text=This project's data products").waitFor();
  await view().getByRole("button", { name: /^Publish to the catalog/ }).click();
  await toast(/^Nothing to publish: every product is a draft$/);
});

test("Link on another team's port names the link after it and sends which environment each reads", { skip }, async () => {
  await openTab("products", "text=Other teams' products");
  await view().locator("tr", { hasText: "finance/ledger" }).getByRole("button", { name: "Link" }).click();
  await dialog().getByText("Name entities use").waitFor();
  assert.strictEqual(await dialog().locator("input.mono").inputValue(), "ledger_accounts");
  assert.deepStrictEqual(await dialog().locator("select").first().locator("option").allTextContents(), ["finance/ledger", "ops/stores"], "drafts can't be linked");
  assert.strictEqual(await dialog().locator("select").nth(1).inputValue(), "accounts");
  await dialog().locator("select").first().selectOption("ops/stores");
  assert.deepStrictEqual(await dialog().locator("select").nth(1).locator("option").allTextContents(), ["stores"]);
  await dialog().locator("select").first().selectOption("finance/ledger");
  await dialog().locator("select").nth(1).selectOption("accounts");
  await dialog().getByLabel("Test reads").selectOption("prod");
  await dialog().getByRole("button", { name: "Add link" }).click();
  await toast(/^Linked\. Use \[source\] link = "ledger_accounts" in an entity\.$/);
  assert.deepStrictEqual(callsOf("mesh_save_link").at(-1).params, { link: { alias: "ledger_accounts", product: "finance/ledger", port: "accounts",
    reads: { dev: "prod", test: "prod", prod: "prod" } } });
});

test("a product's Delete asks first in a danger confirm, then removes it", { skip }, async () => {
  await openTab("products", "text=This project's data products");
  await view().locator("tr.click", { hasText: "Customer 360" }).click();
  await dialog().getByRole("button", { name: "Delete" }).click();
  await page().getByRole("dialog", { name: "Delete Customer 360?" }).waitFor();
  assert.match(await dialog().textContent(), /Teams that link to it keep their links until they remove them/);
  assert.ok(await dialog().locator("button.danger-solid, button.danger").count() > 0, "a danger confirm");
  await dialog().getByRole("button", { name: "Delete" }).click();
  assert.deepStrictEqual((await waitCall("mesh_delete_product", 1)).params, { name: "c360" });
});

test("with no products of its own or from other teams, the tab says so and offers New data product", { skip }, async () => {
  db.overview = { ...overview(), products: [], published: [], problems: [] };
  await openTab("products", "text=No data products yet");
  assert.match(await view().textContent(), /No other team has published a product yet\./);
  assert.strictEqual(await view().locator(".problems").count(), 0);
  await view().locator(".card", { hasText: "No data products yet" }).getByRole("button", { name: "New data product" }).last().click();
  await page().getByRole("dialog", { name: "New data product" }).waitFor();
  assert.strictEqual(await dialog().getByRole("button", { name: "Delete" }).count(), 0);
});

test("Links shows where each link points, who uses it, and Remove removes it", { skip }, async () => {
  db.resolved = [{ alias: "ledger_entries", ok: true, table: "abfss://w-fin@onelake.dfs.fabric.microsoft.com/LH_Gold.Lakehouse/Tables/entries" },
    { alias: "stores", ok: false, message: "ops/stores isn't open to this project" }];
  await openTab("links", "text=Links to other teams' products");
  await view().getByText("Resolved for Dev.").waitFor();
  const rows = await view().locator("tbody tr").allTextContents();
  assert.match(rows[0], /ledger_entriesfinance\/ledger · entriesDev reads Prod, Test reads Test, Prod reads Prodsilver\.ledgerw-fin\/LH_Gold\.Lakehouse\/Tables\/entries/);
  assert.match(rows[1], /storesops\/stores · storesDev reads Prod.*nothing yetops\/stores isn't open to this project/);
  await page().evaluate(() => { window.__realOpen = openEntity; window.openEntity = (n) => window.__led.push(["openEntity", n]); });
  await view().getByRole("button", { name: "silver.ledger" }).click();
  await page().evaluate(() => { window.openEntity = window.__realOpen; });
  assert.deepStrictEqual(await led(), [["openEntity", "silver.ledger"]]);
  await view().locator("tr", { hasText: "stores" }).last().getByRole("button", { name: "Remove" }).click();
  assert.deepStrictEqual((await waitCall("mesh_delete_link", 1)).params, { alias: "stores" });
});

test("a link check the workspace refuses says not checked; no links shows the empty state; nothing to link says so", { skip }, async () => {
  db.resolveFail = true;
  await openTab("links", "text=Links to other teams' products");
  await view().getByText("Not checked for Dev.").waitFor();
  assert.match(await view().locator("tbody tr").first().textContent(), /not checked/);
  db.overview = { ...overview(), links: [], published: [pub("orders/c360"), pub("ops/wip", { status: "draft" })], problems: [] };
  await openTab("links", "text=No links yet");
  assert.match(await view().textContent(), /Find a product on the Data products tab and choose Link, or add one here\./);
  await view().getByRole("button", { name: "New link" }).click();
  await dialog().getByText("No other team has published a product you can link to yet.").waitFor();
});

test("This project: a designation's own workspace can be dropped, and Pick offers a workspace or a new one", { skip }, async () => {
  await openTab("workspaces", "text=Workspaces by designation");
  const row = view().locator("tr", { hasText: "Ingest" });
  assert.match(await row.textContent(), /Ingestbronze.*Orders Ingest - Dev.*Same as Test.*Pick/);
  await row.getByTitle("Use the environment's own workspace").click();
  assert.deepStrictEqual((await waitCall("meta_set_designation", 1)).params, { env: "dev", designation: "ingest" });
  await view().locator("text=Workspaces by designation").waitFor();
  await view().locator("tr", { hasText: "Ingest" }).getByRole("button", { name: "Pick" }).click();
  await page().getByRole("dialog", { name: "Ingest workspace for Prod" }).getByRole("button", { name: "Orders Serve - Prod" }).click();
  assert.deepStrictEqual((await waitCall("meta_set_designation", 2)).params, { env: "prod", designation: "ingest", workspace_id: "w-a", workspace_name: "Orders Serve - Prod" });
  await view().locator("text=Workspaces by designation").waitFor();
  await view().locator("tr", { hasText: "Ingest" }).getByRole("button", { name: "Same as Test" }).click();
  await page().getByRole("dialog", { name: "Ingest workspace for Test" }).getByRole("button", { name: /Create a new workspace/ }).click();
  const create = page().getByRole("dialog", { name: "New Ingest workspace for Test" });
  assert.deepStrictEqual(await create.locator("select option").allTextContents(), ["F8 West (F8)", "No capacity (assign one in Fabric later)"]);
  await create.getByRole("button", { name: /Create workspace/ }).click();
  await toast(/^Created the Ingest workspace for Test$/);
  assert.deepStrictEqual(callsOf("meta_create_designation_workspace").at(-1).params, { env: "test", designation: "ingest", capacity_id: "cap-1" });
});

test("Save sends the project's fields; a domain you don't own waits for its owners", { skip }, async () => {
  db.setProject = { project: { name: "orders" }, domain_request: { id: "r9", detail: { domain_name: "Finance" } } };
  await openTab("workspaces", "text=Project orders");
  await view().getByLabel("Domain", { exact: true }).selectOption("sales");
  await view().getByLabel("Subdomain", { exact: true }).selectOption("sales-eu");
  await view().locator("label", { hasText: "Team" }).locator("input").fill("  Orders crew ");
  await view().locator("label", { hasText: "Description" }).locator("input").fill(" Order facts ");
  await view().getByRole("button", { name: "Save" }).click();
  await toast(/^Saved\. Joining Finance waits for its owners$/);
  assert.deepStrictEqual(callsOf("mesh_set_project").at(-1).params, { fields: { domain: "sales", subdomain: "sales-eu", team: "Orders crew", description: "Order facts" } });
});

test("Fabric's domains for the project's workspaces: a different one, and names the platform doesn't have yet", { skip }, async () => {
  db.fabricDomains = { suggested: { domain: "finance", subdomain: "" }, imported: true, unnamed: [{ env: "prod", workspace_id: "w-p" }], workspaces: [
    { env: "dev", designation: "", workspace_id: "w-dev", workspace_name: "Orders Dev", domain: "finance" },
    { env: "test", designation: "Ingest", workspace_id: "w-ti", workspace_name: "", domain: "sales" }] };
  await openTab("workspaces", ".fabric-domain-hint .note");
  const note = await view().locator(".fabric-domain-hint").textContent();
  assert.match(note, /In Fabric, your Dev workspace \(Orders Dev\) is in the Finance domain\./);
  assert.match(note, /Different domain in Fabric: Test Ingest workspace \(w-ti\) is in Sales\./);
  assert.match(note, /Some of your workspaces are in a Fabric domain the platform doesn't have yet\./);
  await view().locator(".fabric-domain-hint").getByRole("button", { name: "Use it" }).click();
  assert.strictEqual(await view().getByLabel("Domain", { exact: true }).inputValue(), "finance");
  assert.match(await view().locator(".fabric-domain-hint").textContent(), /Picked Finance\. Save to keep it\./);
  db.fabricDomains = { suggested: { domain: "sales", subdomain: "" }, imported: false, unnamed: [{}], workspaces: [
    { env: "dev", designation: "", workspace_id: "w-dev", workspace_name: "Orders Dev", domain: "sales" }] };
  await openTab("workspaces", ".fabric-domain-hint .note");
  assert.match(await view().locator(".fabric-domain-hint").textContent(), /This project matches it\..*ask one to choose Import from Fabric/);
});

test("members that can't be read say so; with none, or before joining, the card says who becomes owner", { skip }, async () => {
  db.members = new Error("The catalog's member table isn't there yet");
  await openTab("workspaces", ".mesh-members");
  await view().getByText(/^This project's members couldn't be read: .*member table isn't there yet/).waitFor();
  db.members = { project: "orders", members: [], earlier: true, can_change: false,
    domain_request: { id: "r2", mine: false, requested_by: "dee@contoso.com", requested_at: new Date().toISOString(), detail: { domain: "finance" } } };
  await openTab("workspaces", ".mesh-members table, .mesh-members p:text('No members yet')");
  const card = await view().locator(".mesh-members").textContent();
  assert.match(card, /From before members: the people who registered or published this project start as its owners/);
  assert.match(card, /No members yet: whoever saves the project next is its first owner\./);
  assert.strictEqual(await view().locator(".mesh-members").getByRole("button", { name: "Add member" }).count(), 0);
  const ask = await view().locator(".mesh-join").textContent();
  assert.match(ask, /Waiting on Finance's owners.*dee@contoso\.com asked .* for orders to join Finance\. It has no owners, so a platform admin decides it on Data mesh > Requests/);
  assert.strictEqual(await view().locator(".mesh-join").getByRole("button", { name: "Withdraw" }).count(), 0);
  db.overview = { ...overview(), project: { ...overview().project, upgraded: false } };
  await openTab("workspaces", ".mesh-members");
  assert.match(await view().locator(".mesh-members").textContent(), /Join the mesh first: then whoever saves it is the project's first owner\./);
  assert.match(await view().textContent(), /Not in the mesh yet/);
  assert.strictEqual(await view().getByRole("button", { name: "Join the mesh" }).count(), 1);
});

test("an entity's About card saves the table's text and only the columns that changed", { skip }, async () => {
  db.about = { description: "", tags: ["pii"], classification: "", steward: "", terms: [], glossary: ["Customer", "Order"],
    products: [{ product: "orders/c360", port: "customers" }], problem: "The catalog is on this PC only", publish_problems: ["Describe every column"],
    columns: [{ name: "id", type: "bigint", description: "", classification: "" }, { name: "email", type: "string", description: "Email", classification: "confidential" },
      { name: "city", type: "string", description: "City", classification: "" }] };
  await mount(`aboutCard({ name: "gold.customers" })`);
  await box().getByRole("button", { name: "Save" }).waitFor();
  const text = await box().textContent();
  assert.match(text, /orders\/c360 · customers/);
  assert.match(text, /The catalog is on this PC only/);
  assert.match(text, /Needed before this port can be published.*Describe every column/);
  assert.strictEqual(await box().locator('input[list="terms-gold.customers"]').getAttribute("placeholder"), "Like Customer, Order");
  await box().locator("textarea").fill(" One row per customer ");
  await box().locator("tr", { hasText: "bigint" }).locator("input").fill("Customer id");
  await box().locator("tr", { hasText: "Email" }).getByLabel("Classification").selectOption("restricted");
  await box().getByRole("button", { name: "Save" }).click();
  await toast(/^Saved to the catalog$/);
  const saves = callsOf("mesh_save_about").slice(-3).map((c) => c.params);
  assert.deepStrictEqual(saves, [
    { name: "gold.customers", fields: { description: "One row per customer", tags: "pii", classification: "", steward: "", terms: "" } },
    { name: "gold.customers", column: "id", fields: { description: "Customer id", classification: "" } },
    { name: "gold.customers", column: "email", fields: { description: "Email", classification: "restricted" } }]);
  db.about = new Error("HTTPError: 403 Client Error: Forbidden for url: https://api.fabric.microsoft.com/v1/x");
  await mount(`aboutCard({ name: "gold.customers" })`);
  await box().getByText(/You don't have permission/).waitFor();
});

test("keys in the platform's database can go back to LH_Keys, after a confirm", { skip }, async () => {
  await mount(`keysPlatformCard({ platform: false, platform_available: false }, () => {})`);
  assert.strictEqual(await box().textContent(), "");
  await mount(`keysPlatformCard({ platform: true, env: "test", moved_at: "${new Date(Date.now() - 3600000).toISOString()}" }, () => window.__led.push(["rerender"]))`);
  assert.match(await box().textContent(), /Keys are in the platform's SQL database.*Moved .*LH_Keys is kept as it was/);
  await box().getByRole("button", { name: "Back to LH_Keys" }).click();
  await page().getByRole("dialog", { name: "Use LH_Keys again?" }).getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(callsOf("meta_keys_to_lakehouse").length, 0);
  await box().getByRole("button", { name: "Back to LH_Keys" }).click();
  await page().getByRole("dialog", { name: "Use LH_Keys again?" }).getByRole("button", { name: "Use LH_Keys" }).click();
  assert.deepStrictEqual((await waitCall("meta_keys_to_lakehouse", 1)).params, { env: "test" });
  await page().waitForFunction(() => window.__led.some((x) => x[0] === "rerender"));
});

test("Move keys to the platform says how many matched, or which didn't and that it stays on LH_Keys", { skip }, async () => {
  const card = `keysPlatformCard({ platform: false, platform_available: true, env: "prod" }, () => window.__led.push(["rerender"]))`;
  db.keysMove = { ok: true, tables: [{ table: "customer", checked: 1200, match: true }, { table: "product", checked: 34, match: true }] };
  await mount(card);
  await box().getByRole("button", { name: "Move keys" }).click();
  await page().getByRole("dialog", { name: "Move Prod's keys to the platform?" }).getByRole("button", { name: "Move keys" }).click();
  await toast(/^Moved 1,234 keys; every one matches$/);
  assert.deepStrictEqual(callsOf("meta_keys_to_platform").at(-1).params, { env: "prod" });
  db.keysMove = { ok: false, tables: [{ table: "customer", checked: 10, match: false, differ: ["C1", "C7"] }, { table: "product", checked: 3, match: true, differ: [] }] };
  await box().getByRole("button", { name: "Move keys" }).click();
  await page().getByRole("dialog", { name: "Move Prod's keys to the platform?" }).getByRole("button", { name: "Move keys" }).click();
  const shown = page().getByRole("dialog", { name: "Keys not switched" });
  await shown.waitFor();
  assert.match(await shown.textContent(), /still uses LH_Keys.*customer: keys C1, C7 differ/);
  assert.doesNotMatch(await shown.textContent(), /product:/);
});

test("Import from Fabric says what was added, matched, skipped and gone, and when nothing changed", { skip }, async () => {
  db.imported = { fabric: 6, owners_unread: ["Ops"], result: { added: ["Ops"], linked: ["Sales", "Finance"], updated: ["HR"], skipped: ["Deep"], gone: ["Old"] } };
  await page().evaluate(() => importFabricDomains(() => window.__led.push(["done"])));
  await page().waitForFunction(() => window.__led.some((x) => x[0] === "done"));
  const toasts = await page().evaluate(() => window.__toasts);
  for (const t of ["Imported 6 Fabric domains: 1 added, 2 matched by name, 1 renamed, moved or with new admins",
    "Skipped (deeper than one subdomain): Deep", "No longer in Fabric, kept here: Old",
    "Domain admins couldn't be read for: Ops. Their owners from Fabric stay as they were."]) assert.ok(toasts.includes(t), `${t} in ${toasts.join(" | ")}`);
  db.imported = { fabric: 2, result: { added: [], linked: [], updated: [], skipped: [], gone: [] } };
  await page().evaluate(() => importFabricDomains(() => {}));
  await toast(/^Imported 2 Fabric domains, nothing changed$/);
});

test("a domain from Fabric keeps Fabric's name and parent; its owners show both sides", { skip }, async () => {
  await page().evaluate(() => domainDialog({ domains: [{ id: "sales", name: "Sales" }, { id: "fin", name: "Finance" }] },
    { id: "fin", name: "Finance", fabric_domain: "f-1", owners: "olu@contoso.com" }, () => {}));
  const d = page().getByRole("dialog", { name: "Edit Finance" });
  assert.match(await d.textContent(), /From Fabric\. Its name and parent follow Fabric's domains/);
  assert.ok(await d.locator("label", { hasText: "Name" }).locator("input").isDisabled());
  assert.strictEqual(await d.locator("label", { hasText: "Name" }).locator("input").getAttribute("title"),
    "Comes from Fabric: rename or move it in the Fabric admin portal, then Import from Fabric");
  assert.strictEqual(await d.getByRole("checkbox").count(), 0, "an existing domain isn't offered to Fabric again");
  await page().evaluate(() => closeDialog());
  await mount(`domainOwnersCell({ owners: "olu@contoso.com", fabric_owners: JSON.stringify([{ name: "Fabric Admin" }]) })`);
  assert.strictEqual(await box().textContent(), "olu@contoso.comIn Fabric: Fabric Admin");
  await mount(`domainOwnerLine({ owners: "", fabric_owners: JSON.stringify([{ name: "Ann" }, { name: "Bo" }, { name: "Cy" }]) })`);
  assert.strictEqual(await box().textContent(), "Owner: Ann +2 (from Fabric)");
});

test("the mesh commands go to Products and links, and to Platform > Settings", { skip }, async () => {
  const run = (label) => page().evaluate((label) => features.commands.find((c) => c.label === label).run(), label);
  assert.strictEqual(await page().evaluate(() => features.commands.find((c) => c.label === "New data product").when()), true);
  await run("New data product");
  await view().locator("text=This project's data products").waitFor();
  await run("Link another team's product");
  await view().locator("text=Links to other teams' products").waitFor();
  await page().evaluate(() => { window.__realGoPlatform = goPlatform; window.goPlatform = (t) => window.__led.push(["goPlatform", t]); });
  await run("Set up the platform");
  await page().evaluate(() => { window.goPlatform = window.__realGoPlatform; });
  assert.deepStrictEqual(await led(), [["goPlatform", "settings"]]);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

// Platform (platform.js) beyond the other platform and mesh tests, with every engine call mocked:
// the first admin claim, Business keys for admins and for teams, the source request with its Copy job,
// a source's Copy job run, the request rows and what approving or declining each kind says first
// (shortcuts, Copy jobs, workspaces without a capacity, workspace access, what a declined approval left
// in Fabric), Withdraw, the New request forms for workspace access and domains, the glossary, and the
// platform admins (see harness.cjs for how to run it).
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const PC = { kind: "pc" };
const overview = (p) => ({ env: p.env, counts: { domains: 1, subdomains: 0, projects: 0, teams: 0, key_tables: 0, keys: 0, products: 0, published: 0, requests: 0 },
  landing: { sources: [] }, problems: [], me: { admin: false }, needs_you: [], mine: [],
  status: { source: "settings", default_names: {}, environments: { dev: PC, test: PC, prod: PC } }, recent: [] });
const KEYS = { env: "dev", shared: false, key_tables: [
  { name: "customer", business_key: "customer_id", description: "A customer, from any source", keys: 10442, next: 10443, created_at: "2026-10-01T09:00:00Z", created_by: "ana@contoso.com" },
  { name: "supplier", keys: 0 }],
requested: [{ id: "k1", kind: "key_table", target: "product", status: "waiting", mine: true, requested_by: "lee@contoso.com", requested_at: "2026-11-01T09:00:00Z" }] };
const at = "2026-11-18T09:00:00Z";
const req = (id, kind, target, detail, more = {}) => ({ id, kind, target, detail, status: "waiting", can_decide: true, requested_by: "lee@contoso.com", requested_at: at, ...more });
const ALEX = { type: "User", email: "alex@contoso.com", name: "Alex" };
const REQUESTS = [
  req("sc", "shortcut", "sales_orders", { name: "sales_orders", workspace_name: "Finance Dev", lakehouse_name: "LH_Finance" }),
  req("cj", "source", "erp", { owner: "erp-team@contoso.com", copy: { connection_name: "SQL ERP: erp.contoso.com", times: ["02:00"],
    tables: [{ table: "dbo.Customer", folder: "Customer", mode: "full" }, { table: "dbo.Order", folder: "Order", mode: "incremental", column: "ModifiedAt" }] } }),
  req("ws", "workspace", "Churn - Dev", { name: "Churn - Dev", admins: [{ email: "ana@contoso.com" }] }),
  req("keep", "workspace_access", "Sales Prod", { workspace_name: "Sales Prod", role: "Viewer", principal: ALEX }, { today: { checked: true, role: "Member" } }),
  req("raise", "workspace_access", "Sales Dev", { workspace_name: "Sales Dev", role: "Admin", principal: { type: "Group", name: "Sales Engineers" } }, { today: { checked: true, role: "Contributor" } }),
  req("none", "workspace_access", "Ops", { workspace_name: "Ops", role: "Contributor", principal: ALEX }, { today: { checked: true, role: "" } }),
  req("unread", "workspace_access", "HR", { workspace_name: "HR", role: "Viewer", principal: ALEX }, { today: { checked: false } }),
  req("join", "join_domain", "crm", { domain_name: "Sales", subdomain: "EMEA" }),
  req("data", "data", "sales", { what: "new", domain_name: "Sales", needed_by: "2026-12-01" }),
  req("mine", "key_table", "product", {}, { can_decide: false, mine: true }),
];
// Requests whose approval made something in Fabric before a later step failed.
const MADE = [
  req("m-ws", "workspace", "Old WS", { name: "Old WS", made: { workspace_id: "w9" } }),
  req("m-gave", "workspace_access", "Sales Prod", { workspace_name: "Sales Prod", role: "Viewer", principal: ALEX, made: { assigned: true, change: "gave" } }),
  req("m-raised", "workspace_access", "Sales Dev", { workspace_name: "Sales Dev", role: "Admin", principal: { type: "Group", name: "Eng" }, made: { assigned: true, change: "raised", from: "Viewer" } }),
  req("m-same", "workspace_access", "Ops", { workspace_name: "Ops", role: "Member", principal: ALEX, made: { assigned: true, change: "same" } }),
  req("m-cj", "source", "erp", { copy: { tables: [] }, made: { copy_job_id: "cj1", copy_job_name: "CJ_Landing_erp", schedule_id: "s1" } }),
  req("m-sc", "shortcut", "orders", { name: "orders", lakehouse_name: "LH_Sales", made: { shortcut_id: "x", found: true,
    also: [{ env: "test", workspace_name: "Sales Test", lakehouse_name: "LH_Sales" }] } }),
  req("m-dom", "domain", "emea", { name: "EMEA", made: { domain_id: "emea", fabric_domain_id: "f1" } }),
  req("m-dom2", "domain", "apac", { name: "APAC", made: { domain_id: "apac", found: true } }),
];

let me, requests, decided, sentReqs, calls, keys, glossary, admins, over;
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  over = {};
  const m = {
    platform_whoami: () => me,
    platform_overview: overview,
    platform_note_seen: () => ({}),
    platform_owner_coverage: () => ({ available: false, problem: "Not set up" }),
    readiness_check: () => [],
    platform_claim_admin: () => { me = { ...me, admin: true, can_claim: false }; return { ok: true }; },
    platform_key_tables: () => keys,
    platform_create_key_table: (p) => ({ name: p.name, environments: [{ env: "dev", ok: true }, { env: "prod", ok: false, message: "SQL_Platform isn't reachable" }] }),
    platform_request: (p) => { sentReqs.push(p); return { id: "new", target: p.target }; },
    platform_requests: () => requests,
    platform_decide: (p) => { decided.push(p); return { status: "approved", problems: p.id === "join" ? ["Teams notice not sent"] : [], next: [] }; },
    platform_withdraw: () => ({ next: ["The Copy job it made stays"] }),
    platform_domains: () => [{ id: "sales", name: "Sales" }, { id: "emea", name: "EMEA", parent_id: "sales" }],
    platform_glossary: () => glossary,
    platform_save_term: (p) => { glossary = [...glossary.filter((t) => t.term !== p.term.term), p.term]; return { ok: true }; },
    platform_admins: () => admins,
    platform_add_admin: ({ user }) => { admins = [...admins, { user_name: user, added_at: at, added_by: "ana@contoso.com" }]; return { ok: true }; },
    platform_remove_admin: ({ user }) => { admins = admins.filter((a) => a.user_name !== user); return { ok: true }; },
    platform_status: () => ({ source: "settings", default_names: {}, environments: { dev: PC, test: PC, prod: PC }, landing: { central: true, workspace_name: "Landing" } }),
    shortcut_request_envs: () => ({ env: "dev", places: [
      { env: "test", workspace_name: "Finance Test", lakehouse_name: "LH_Finance", target_workspace_name: "Sales Test", target_lakehouse_name: "LH_Sales" },
      { env: "prod", why: "Prod has no lakehouse named LH_Finance" }] }),
    list_capacities: () => [{ id: "cap1", displayName: "Shared F64" }],
    list_workspaces: () => [{ id: "w1", displayName: "Sales Prod" }],
    fabric_connections: () => ({ connections: [{ id: "c1", name: "SQL ERP", type: "SQL", path: "erp.contoso.com" }, { id: "c2", name: "Blob", type: "AzureBlobs" }] }),
    platform_landing: () => ({ env: "dev", kind: "pc", where: "C:/landing", central: false, sources: [{ name: "erp", code: "ERP", registered: true, source_id: 3,
      entities: [], readers: [], late: false, copy_job: { name: "CJ_Landing_erp", id: "cj1", workspace_id: "wl", tables: 2, times: [] } }], system_types: [], zones: [] }),
    platform_landing_files: () => [],
    risk_check: () => ({ result: "pass", can_proceed: true, findings: [] }),
    run_fabric_item_now: () => ({ started: true }),
    mesh_map: () => ({ domains: [], projects: [], products: [] }),
    platform_save_source: () => ({ ok: true }),
    platform_delete_source: () => ({ ok: true }),
    platform_audit: () => [],
  };
  const mocks = {};
  for (const [k, fn] of Object.entries(m)) mocks[k] = (p) => (over[k] ? over[k](p) : fn(p));
  app = await openApp({ mocks });
  calls = app.calls;
});

after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  over = {};
  me = { admin: true, user: "ana@contoso.com", domains_owned: [] };
  requests = REQUESTS;
  decided = [];
  sentReqs = [];
  keys = KEYS;
  glossary = [];
  admins = [];
  await page().evaluate(() => {
    closeDialog();
    document.querySelector("#toast")?.replaceChildren();
    state.user = "ana@contoso.com";
    Object.assign(platformPage, { env: "dev", keysShared: false, landingCentral: false, source: null, me: null });
    meshRequests.show = "all";
    go("home");
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

const sent = (method) => calls.filter((c) => c.method === method).map((c) => c.params);
const dialog = (name) => page().getByRole("dialog", { name });
const row = (id) => page().locator(`tr[data-request="${id}"]`);
async function toastSays(re) {
  await page().waitForFunction((src) => new RegExp(src).test(document.querySelector("#toast")?.innerText || ""), re.source);
  return page().locator("#toast").innerText();
}
async function until(fn, what) {
  for (const end = Date.now() + 10000; !fn(); await new Promise((r) => setTimeout(r, 20))) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
  }
}
async function requestsTab() {
  await page().evaluate(() => goMeta("requests"));
  await row("sc").or(row("m-ws")).first().waitFor();
}

test("A platform with no admin offers to become its first one; without the engine the page says so", { skip }, async () => {
  me = { admin: false, can_claim: true, user: "ana@contoso.com" };
  await page().evaluate(() => goPlatform("overview"));
  await page().getByRole("link", { name: "Become its first admin" }).click();
  await toastSays(/^You're the platform's first admin/);
  assert.strictEqual(sent("platform_claim_admin").length, 1);
  await page().waitForFunction(() => /You're a platform admin: you create domains/.test(document.querySelector(".sub")?.textContent || ""));
  over.platform_whoami = () => { throw new Error("ConnectionError: the engine isn't running"); };
  await page().evaluate(() => render());
  await page().getByText("The platform shows once the local engine is running.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Business keys: an admin sees every key table and makes one, and hears where it failed", { skip }, async () => {
  await page().evaluate(() => goPlatform("keys"));
  const card = page().locator(".card:not(.how-it-works)", { hasText: "Key tables in SQL_Platform (Dev)" });
  await card.waitFor();
  const text = await card.innerText();
  assert.match(text, /sk\.customer\s+customer_id\s+A customer, from any source\s+10,442\s+10,443/);
  assert.match(text, /sk\.supplier\s+supplier_bk/, "a key table without its own business key column name uses <name>_bk");
  assert.match(text, /To give every environment the same keys, use Move keys in/);
  assert.match(text, /Requested[\s\S]*product/);
  await card.getByRole("button", { name: "New key table" }).click();
  const d = dialog("New key table");
  assert.match(await d.innerText(), /Creates sk\.<name> in every environment's SQL_Platform/);
  await d.getByRole("button", { name: "Create" }).click();  // no name: nothing sent
  assert.strictEqual(sent("platform_create_key_table").length, 0);
  await d.locator("input").nth(0).fill("store");
  await d.locator("input").nth(1).fill("store_code");
  await d.locator("input").nth(2).fill("A shop");
  await d.getByRole("button", { name: "Create" }).click();
  await toastSays(/^Created sk\.store, but not in Prod: SQL_Platform isn't reachable/);
  assert.deepStrictEqual(sent("platform_create_key_table"), [{ name: "store", business_key: "store_code", description: "A shop" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Business keys: shared keys hide the environment, and a team asks for a key table", { skip }, async () => {
  me = { admin: false, user: "lee@contoso.com" };
  keys = { ...KEYS, shared: true, requested: [] };
  await page().evaluate(() => goPlatform("keys"));
  await page().getByText("Shared by Dev, Test and Prod").waitFor();
  assert.strictEqual(await page().getByRole("group", { name: "Environment" }).count(), 0);
  assert.match(await page().locator(".card:not(.how-it-works)", { hasText: "Key tables in SQL_Keys" }).innerText(), /Only platform admins add key tables/);
  await page().getByRole("button", { name: "Request a key table" }).click();
  const d = dialog("Request a key table");
  await d.getByRole("button", { name: "Send request" }).click();  // no name yet
  assert.strictEqual(sentReqs.length, 0);
  await d.locator("input").nth(0).fill("supplier");
  await d.locator("input").nth(1).fill("supplier_id");
  await d.locator("textarea").fill("For the buying team");
  await d.getByRole("button", { name: "Send request" }).click();
  await toastSays(/^Request sent/);
  assert.deepStrictEqual(sentReqs, [{ kind: "key_table", target: "supplier", reason: "For the buying team",
    detail: { business_key: "supplier_id", description: "For the buying team" } }]);
  // No key tables at all.
  keys = { env: "dev", shared: false, key_tables: [], requested: [] };
  await page().evaluate(() => render());
  await page().getByText("No key tables yet.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Request a source asks for a Copy job only when ticked, with the tables and the SQL connection picked", { skip }, async () => {
  me = { admin: false, user: "lee@contoso.com" };
  await page().evaluate(() => goPlatform("landing"));
  await page().getByRole("button", { name: "Request a source" }).click();
  const d = dialog("Request a source");
  const conn = d.getByRole("combobox", { name: "Fabric connection" });
  await conn.locator("option", { hasText: "SQL ERP (SQL)" }).waitFor({ state: "attached" });
  assert.deepStrictEqual(await conn.locator("option").allInnerTexts(), ["Pick one", "SQL ERP (SQL)"], "only SQL connections");
  await d.getByRole("textbox", { name: "Folder name" }).fill("erp");
  await d.getByRole("textbox", { name: "Owner" }).fill("erp-team@contoso.com");
  await d.getByRole("button", { name: "Send request" }).click();
  assert.strictEqual(sentReqs.length, 0, "a Copy job needs its connection");
  await conn.selectOption("c1");
  await d.getByRole("button", { name: "Send request" }).click();
  assert.strictEqual(sentReqs.length, 0, "and a table");
  await d.getByRole("textbox", { name: "Table" }).fill("dbo.Customer");
  await d.getByRole("button", { name: "Add a table" }).click();
  await d.getByRole("button", { name: "Add a table" }).click();
  const tables = d.getByRole("textbox", { name: "Table" });
  await tables.nth(1).fill("dbo.Order");
  const column = d.getByRole("textbox", { name: "Column" }).nth(1);
  assert.strictEqual(await column.isDisabled(), true, "a whole-table copy has no column");
  await d.getByRole("combobox", { name: "Each run copies" }).nth(1).selectOption("incremental");
  assert.strictEqual(await column.isDisabled(), false);
  await column.fill("ModifiedAt");
  await d.getByRole("button", { name: "Remove this table" }).nth(2).click();
  assert.strictEqual(await tables.count(), 2);
  await d.getByRole("textbox", { name: "Why" }).fill("Orders for the sales team");
  await d.getByRole("button", { name: "Send request" }).click();
  await toastSays(/^Request sent/);
  assert.deepStrictEqual(sentReqs[0], { kind: "source", target: "erp", reason: "Orders for the sales team",
    detail: { owner: "erp-team@contoso.com", expected_hours: "24", copy: { connection_id: "c1", connection_name: "SQL ERP: erp.contoso.com",
      tables: [{ table: "dbo.Customer", mode: "full", column: "" }, { table: "dbo.Order", mode: "incremental", column: "ModifiedAt" }], times: ["02:00"] } } });
  // Without a Copy job, only the source is asked for.
  await page().getByRole("button", { name: "Request a source" }).click();
  await d.getByRole("textbox", { name: "Folder name" }).fill("crm");
  await d.getByRole("checkbox").uncheck();
  assert.strictEqual(await d.getByRole("combobox", { name: "Fabric connection" }).isHidden(), true);
  await d.getByRole("button", { name: "Send request" }).click();
  await until(() => sentReqs.length === 2, "the second request");
  assert.deepStrictEqual(sentReqs[1].detail, { owner: "", expected_hours: "24" });
  assert.deepStrictEqual(app.errors, []);
});

test("A source landed by a Copy job says so, and Run now confirms before it starts the job", { skip }, async () => {
  await page().evaluate(() => goPlatform("landing"));
  const line = page().locator(".copy-job-line");
  await line.waitFor();
  assert.match(await line.innerText(), /Lands with CJ_Landing_erp[\s\S]*A Copy job made from a request: 2 tables, no schedule\./);
  await line.getByRole("button", { name: "Run now..." }).click();
  const d = dialog("Run CJ_Landing_erp now?");
  assert.match(await d.innerText(), /It copies 2 tables into the landing zone in Fabric\. It uses capacity, and the gateway reads the source\./);
  await d.getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(sent("run_fabric_item_now").length, 0);
  await line.getByRole("button", { name: "Run now..." }).click();
  await d.getByRole("button", { name: "Run now" }).click();
  await toastSays(/^CJ_Landing_erp started; it shows in Monitor > Runs/);
  assert.deepStrictEqual(sent("run_fabric_item_now"), [{ workspace_id: "wl", item_id: "cj1", job_type: "Execute", name: "CJ_Landing_erp", item_type: "CopyJob" }]);
  assert.deepStrictEqual(sent("risk_check")[0], { id: "fabric:wl:cj1", action: "run_item", env: null, refresh: false });
  // A team member sees the line without the button.
  me = { admin: false, user: "lee@contoso.com" };
  await page().evaluate(() => render());
  await page().locator(".copy-job-line").waitFor();
  assert.strictEqual(await page().locator(".copy-job-line").getByRole("button").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Each request row says what it asks for, and what approving a workspace access request changes", { skip }, async () => {
  await requestsTab();
  assert.match(await row("sc").innerText(), /Shortcut\s+sales_orders in Finance Dev \/ LH_Finance/);
  assert.match(await row("cj").innerText(), /Copy job: 2 tables from SQL ERP: erp\.contoso\.com, daily at 02:00 UTC/);
  assert.match(await row("cj").innerText(), /Approve and make it/);
  assert.match(await row("ws").innerText(), /capacity: the approver picks, admins ana@contoso\.com/);
  assert.match(await row("keep").innerText(), /alex@contoso\.com has Member in Sales Prod today, so approving keeps Member\./);
  assert.match(await row("raise").innerText(), /Sales Engineers has Contributor in Sales Dev today; approving raises it to Admin\./);
  assert.match(await row("none").innerText(), /alex@contoso\.com has no role of its own in Ops today\. A role through another group isn't counted\./);
  assert.match(await row("unread").innerText(), /Couldn't check today's role: Fabric didn't answer\./);
  assert.match(await row("join").innerText(), /crm into Sales \/ EMEA[\s\S]*Domain owners: none yet, so a platform admin decides/);
  assert.match(await row("data").innerText(), /New data product from Sales[\s\S]*Needed by 2026-12-01[\s\S]*Accept/);
  assert.match(await row("mine").innerText(), /Key table\s+product[\s\S]*Waiting[\s\S]*Withdraw/);
  // Approving: keeps the role, raises it, or gives it.
  const confirmText = async (id, title) => {
    await row(id).getByRole("button", { name: "Approve", exact: true }).click();
    const d = dialog(title);
    const text = await d.innerText();
    await d.getByRole("button", { name: "Cancel" }).click();
    await d.waitFor({ state: "detached" });
    return text;
  };
  assert.match(await confirmText("keep", "Approve Viewer on Sales Prod?"),
    /alex@contoso\.com already has Member in Sales Prod, which is higher than Viewer\. Approving keeps Member and marks the request approved\. Nothing changes in Fabric\./);
  assert.match(await confirmText("raise", "Approve Admin on Sales Dev?"), /As you, KratosForge raises the group Sales Engineers from Contributor to Admin in Sales Dev\./);
  assert.match(await confirmText("unread", "Approve Viewer on HR?"), /gives alex@contoso\.com the Viewer role in HR\. If they already have a higher role, they keep it\./);
  assert.deepStrictEqual(decided, [], "Cancel decides nothing");
  await row("none").getByRole("button", { name: "Approve", exact: true }).click();
  await dialog("Approve Contributor on Ops?").getByRole("button", { name: "Add and approve" }).click();
  await until(() => decided.length === 1, "the decision");
  assert.deepStrictEqual(decided[0], { id: "none", approve: true, note: "" });
  await toastSays(/^Approved Ops/);
  assert.deepStrictEqual(app.errors, []);
});

test("Approving a shortcut names every environment it's made in; a Copy job names its tables and zone", { skip }, async () => {
  await requestsTab();
  await row("sc").getByRole("button", { name: "Approve", exact: true }).click();
  const d = dialog("Approve shortcut sales_orders?");
  const text = await d.innerText();
  assert.match(text, /Dev: Finance Dev \/ LH_Finance, as asked/);
  assert.match(text, /Test: Finance Test \/ LH_Finance, pointing at Sales Test \/ LH_Sales/);
  assert.match(text, /Prod: not made\. Prod has no lakehouse named LH_Finance\./);
  assert.deepStrictEqual(sent("shortcut_request_envs"), [{ id: "sc" }]);
  await d.getByRole("button", { name: "Make and approve" }).click();
  await toastSays(/^Approved shortcut sales_orders/);
  assert.deepStrictEqual(decided.at(-1), { id: "sc", approve: true, note: "" });
  // The engine couldn't work out the other environments.
  over.shortcut_request_envs = () => { throw new Error("RuntimeError: no"); };
  await row("sc").getByRole("button", { name: "Approve", exact: true }).click();
  await d.getByText("The other environments couldn't be worked out now; approving says what it made.").waitFor();
  await d.getByRole("button", { name: "Cancel" }).click();

  // A Copy job with one landing workspace: where it lands comes from the request.
  requests = [{ ...REQUESTS[1], lands_in: { lakehouse_name: "LH_Landing_ERP", zone_name: "ERP", zone: "erp", secure: true, workspace_name: "Landing" } }];
  await page().evaluate(() => render());
  await row("cj").getByRole("button", { name: "Approve and make it" }).click();
  const cj = dialog("Approve source erp?");
  const cjText = await cj.innerText();
  assert.match(cjText, /The source erp in the landing zone, owned by erp-team@contoso\.com\./);
  assert.match(cjText, /CJ_Landing_erp reading SQL ERP: erp\.contoso\.com/);
  assert.match(cjText, /dbo\.Customer\s+Files\/erp\/Customer\/\s+the whole table/);
  assert.match(cjText, /dbo\.Order\s+Files\/erp\/Order\/\s+rows changed since the last run, by ModifiedAt/);
  assert.match(cjText, /Its schedule: every day at 02:00 UTC\./);
  assert.match(cjText, /It writes to LH_Landing_ERP, the secure zone ERP, in Landing\. Only the readers its data access role allows see it\./);
  await cj.getByRole("button", { name: "Approve and make it" }).click();
  await until(() => decided.length === 2, "the Copy job decision");
  assert.deepStrictEqual(decided.at(-1), { id: "cj", approve: true, note: "" });
  // A zone that couldn't be read, a zone with a problem, the Shared zone.
  for (const [lands, re] of [[undefined, /In Landing; its zone couldn't be read, so check the request before approving\./],
    [{ problem: "The zone erp was removed." }, /The zone erp was removed\./],
    [{ zone: "shared", zone_name: "Shared", lakehouse_name: "LH_Landing" }, /Every domain, and Dev and Test, read the Shared zone\./]]) {
    requests = [{ ...REQUESTS[1], lands_in: lands }];
    await page().evaluate(() => render());
    await row("cj").getByRole("button", { name: "Approve and make it" }).click();
    assert.match(await cj.innerText(), re);
    await cj.getByRole("button", { name: "Cancel" }).click();
    await cj.waitFor({ state: "detached" });
  }
  // No one landing workspace yet: the approver picks the environment.
  over.platform_status = () => ({ landing: { central: false } });
  await row("cj").getByRole("button", { name: "Approve and make it" }).click();
  await cj.getByRole("combobox", { name: "Environment" }).selectOption("test");
  await cj.getByRole("button", { name: "Approve and make it" }).click();
  await until(() => decided.length === 3, "the decision with an environment");
  assert.deepStrictEqual(decided.at(-1), { id: "cj", approve: true, note: "", env: "test" });
  assert.deepStrictEqual(app.errors, []);
});

test("A workspace request without a capacity asks for one, or says there is none to use", { skip }, async () => {
  await requestsTab();
  await row("ws").getByRole("button", { name: "Approve and create" }).click();
  const pick = dialog("Pick a capacity for Churn - Dev");
  await pick.getByRole("combobox", { name: "Capacity" }).waitFor();
  await pick.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(decided, []);
  await row("ws").getByRole("button", { name: "Approve and create" }).click();
  await pick.getByRole("button", { name: "Next" }).click();
  const sure = dialog("Approve and create Churn - Dev?");
  assert.match(await sure.innerText(), /creates the workspace on Shared F64 and makes ana@contoso\.com its admins/);
  await sure.getByRole("button", { name: "Create and approve" }).click();
  await until(() => decided.length === 1, "the decision");
  assert.deepStrictEqual(decided[0], { id: "ws", approve: true, note: "", capacity_id: "cap1" });
  over.list_capacities = () => [];
  await row("ws").getByRole("button", { name: "Approve and create" }).click();
  await toastSays(/^No capacity you can put a workspace on\. Ask for Contributor on one, then approve\./);
  assert.strictEqual(decided.length, 1);
  assert.deepStrictEqual(app.errors, []);
});

test("Accepting a data request needs a reply; a decision with problems says them", { skip }, async () => {
  await requestsTab();
  await row("data").getByRole("button", { name: "Accept" }).click();
  const d = dialog("Accept the data request from lee@contoso.com?");
  await d.getByRole("button", { name: "Accept" }).click();
  assert.strictEqual(await d.isVisible(), true, "no reply, still open");
  await d.getByRole("textbox", { name: "Reply" }).fill("In Prod by 30 November");
  await d.getByRole("button", { name: "Accept" }).click();
  await until(() => decided.length === 1, "the acceptance");
  assert.deepStrictEqual(decided[0], { id: "data", approve: true, note: "In Prod by 30 November" });
  await row("join").getByRole("button", { name: "Approve", exact: true }).click();
  await toastSays(/^Approved crm; Teams notice not sent/);
  // Withdraw a request of your own.
  await row("mine").getByRole("button", { name: "Withdraw" }).click();
  await toastSays(/^Withdrew product; The Copy job it made stays/);
  assert.deepStrictEqual(sent("platform_withdraw"), [{ id: "mine" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Declining a request whose approval made something says what is still in Fabric", { skip }, async () => {
  requests = MADE;
  await requestsTab();
  const said = async (id, title) => {
    await row(id).getByRole("button", { name: "Decline" }).click();
    const d = dialog(title);
    const text = await d.locator(".wrn-text").innerText();
    await d.getByRole("button", { name: "Cancel" }).click();
    await d.waitFor({ state: "detached" });
    return text;
  };
  assert.match(await said("m-ws", "Decline workspace Old WS?"), /^Declining deletes nothing in Fabric\. The workspace Old WS, made by this request, is still in Fabric on its capacity/);
  assert.match(await said("m-gave", "Decline workspace access Sales Prod?"), /Alex keeps the Viewer role in Sales Prod that approving this request gave/);
  assert.match(await said("m-raised", "Decline workspace access Sales Dev?"), /The group Eng keeps the Admin role in Sales Dev that approving this request raised from Viewer/);
  assert.match(await said("m-same", "Decline workspace access Ops?"), /Alex has the Member role in Ops: they had it before this request, or approving it gave it/);
  assert.match(await said("m-cj", "Decline source erp?"), /The Copy job CJ_Landing_erp, made by this request, is still in the landing workspace and its daily schedule still runs it/);
  const sc = await said("m-sc", "Decline shortcut orders?");
  assert.match(sc, /The shortcut orders in LH_Sales is in OneLake: it was there before this request/);
  assert.match(sc, /The shortcut orders in Sales Test \/ LH_Sales \(Test\), made by this request, is still in OneLake/);
  assert.match(await said("m-dom", "Decline domain emea?"), /The domain EMEA, made by this request, is still in Fabric and the platform/);
  assert.match(await said("m-dom2", "Decline domain apac?"), /The domain APAC is in the platform: it was there before this request/);
  assert.deepStrictEqual(decided, []);
  await row("m-dom2").getByRole("button", { name: "Decline" }).click();
  await dialog("Decline domain apac?").getByRole("textbox").fill("Use EMEA instead");
  await dialog("Decline domain apac?").getByRole("button", { name: "Decline" }).click();
  await until(() => decided.length === 1, "the decline");
  assert.deepStrictEqual(decided[0], { id: "m-dom2", approve: false, note: "Use EMEA instead" });
  await toastSays(/^Declined apac/);
  assert.deepStrictEqual(app.errors, []);
});

test("New request: access to a workspace for a group, or one not listed; a domain under a parent", { skip }, async () => {
  requests = [];
  await page().evaluate(() => goMeta("requests"));
  await page().getByRole("button", { name: "New request" }).click();
  const d = dialog("New request");
  await d.getByRole("button", { name: /Access to a workspace/ }).click();
  const ws = d.getByRole("combobox", { name: "Workspace" });
  await ws.waitFor();
  assert.strictEqual(await d.getByRole("textbox", { name: "Workspace ID" }).isHidden(), true);
  await d.getByRole("combobox", { name: "Role" }).selectOption("Contributor");
  await d.getByRole("group", { name: "Who" }).getByRole("button", { name: "A group" }).click();
  await d.getByRole("button", { name: "Send request" }).click();
  assert.strictEqual(sentReqs.length, 0, "a group needs its object ID");
  await d.getByRole("textbox", { name: "Group object ID" }).fill("g-123");
  await d.getByRole("textbox", { name: "Group name" }).fill("Sales Engineers");
  await d.getByRole("button", { name: "Send request" }).click();
  await toastSays(/^Request sent: workspace access Sales Prod/);
  assert.deepStrictEqual(sentReqs[0], { kind: "workspace_access", target: "Sales Prod", reason: "",
    detail: { workspace_id: "w1", workspace_name: "Sales Prod", role: "Contributor", who: "group", group_id: "g-123", group_name: "Sales Engineers" } });

  await page().getByRole("button", { name: "New request" }).click();
  await d.getByRole("button", { name: /Access to a workspace/ }).click();
  await ws.selectOption("other");
  await d.getByRole("button", { name: "Send request" }).click();
  assert.strictEqual(sentReqs.length, 1, "another workspace needs its ID");
  await d.getByRole("textbox", { name: "Workspace ID" }).fill("w-77");
  await d.getByRole("button", { name: "Send request" }).click();
  await until(() => sentReqs.length === 2, "the second request");
  assert.deepStrictEqual(sentReqs[1].detail, { workspace_id: "w-77", workspace_name: "w-77", role: "Viewer", who: "me" });

  await page().getByRole("button", { name: "New request" }).click();
  await d.getByRole("button", { name: /A domain/ }).click();
  const parent = d.getByRole("combobox", { name: "Parent" });
  await parent.waitFor();
  assert.deepStrictEqual(await parent.locator("option").allInnerTexts(), ["None: a top-level domain", "Sales"], "subdomains can't be parents");
  await d.getByRole("button", { name: "Send request" }).click();
  assert.strictEqual(sentReqs.length, 2, "a domain needs a name");
  await d.getByRole("textbox", { name: "Domain name" }).fill("Retail");
  await parent.selectOption("sales");
  await d.getByRole("textbox", { name: "Owners" }).fill("ana@contoso.com");
  await d.getByRole("button", { name: "Send request" }).click();
  await until(() => sentReqs.length === 3, "the domain request");
  assert.deepStrictEqual(sentReqs[2], { kind: "domain", target: "Retail", reason: "", detail: { name: "Retail", parent_id: "sales", description: "", owners: "ana@contoso.com" } });
  // The older kinds are a link away.
  await page().getByRole("button", { name: "New request" }).click();
  await d.getByRole("button", { name: "Key table", exact: true }).click();
  await dialog("Request a key table").getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("The glossary: an admin adds and edits terms; a domain owner keeps only their domain's", { skip }, async () => {
  glossary = [{ term: "Active customer", definition: "Bought in the last 90 days", owner: "Sales", domain_id: "sales" },
    { term: "Churn", definition: "Left in the month", owner: "Data team", domain_id: "" }];
  await page().evaluate(() => goPlatform("glossary"));
  const card = page().locator(".card", { hasText: "Business glossary" });
  await card.getByText("Active customer", { exact: true }).waitFor();
  assert.match(await card.innerText(), /Active customer\s+Bought in the last 90 days\s+Sales\s+Sales/);
  assert.match(await card.innerText(), /Churn\s+Left in the month\s+shared\s+Data team/);
  await card.getByRole("button", { name: "New term" }).click();
  const d = dialog("New glossary term");
  await d.locator("input").nth(0).fill("Order");
  await d.locator("textarea").fill("A confirmed sale");
  await d.locator("select").selectOption("");
  await d.getByRole("button", { name: "Save" }).click();
  await card.getByText("A confirmed sale").waitFor();
  assert.deepStrictEqual(sent("platform_save_term")[0], { term: { term: "Order", definition: "A confirmed sale", owner: "", domain_id: "" } });
  await card.getByText("Churn", { exact: true }).click();
  const e = dialog("Churn");
  assert.strictEqual(await e.locator("input").nth(0).isDisabled(), true, "a term's name doesn't change");
  await e.locator("textarea").fill("Left during the month");
  await e.getByRole("button", { name: "Save" }).click();
  await card.getByText("Left during the month").waitFor();
  // A domain owner edits their domain's terms only, and can't make shared ones.
  me = { admin: false, user: "lee@contoso.com", domains_owned: ["sales"] };
  await page().evaluate(() => { platformPage.me = null; render(); });
  await card.getByText("Active customer", { exact: true }).waitFor();
  assert.strictEqual(await card.locator("tr.click").count(), 1);
  await card.getByRole("button", { name: "New term" }).click();
  assert.deepStrictEqual(await dialog("New glossary term").locator("select option").allInnerTexts(), ["Sales"]);
  await dialog("New glossary term").getByRole("button", { name: "Cancel" }).click();
  // Someone who owns nothing only reads.
  me = { admin: false, user: "kim@contoso.com", domains_owned: [] };
  glossary = [];
  await page().evaluate(() => render());
  await card.getByText("No terms yet.").waitFor();
  assert.strictEqual(await card.getByRole("button", { name: "New term" }).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Settings: admins add and remove admins, and removing asks first", { skip }, async () => {
  admins = [{ user_name: "ana@contoso.com", added_at: at, added_by: "ana@contoso.com" }, { user_name: "lee@contoso.com", added_at: at, added_by: "ana@contoso.com" }];
  await page().evaluate(() => goPlatform("settings"));
  const card = page().locator("section", { hasText: "Platform admins" });
  await card.getByText("(you)").waitFor();
  assert.match(await card.innerText(), /lee@contoso\.com\s+Added .* by ana@contoso\.com/);
  await card.getByRole("button", { name: "Add admin" }).click();
  assert.strictEqual(sent("platform_add_admin").length, 0, "no name, nothing sent");
  await card.getByRole("textbox", { name: "New admin" }).fill("kim@contoso.com");
  await card.getByRole("button", { name: "Add admin" }).click();
  await card.getByText("kim@contoso.com").waitFor();
  const removeLee = card.locator("tr", { hasText: "lee@contoso.com" }).getByRole("button", { name: "Remove" });
  await removeLee.click();
  const d = dialog("Remove lee@contoso.com as a platform admin?");
  assert.match(await d.getByRole("button", { name: "Remove" }).getAttribute("class"), /danger/);
  await d.getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(sent("platform_remove_admin").length, 0);
  await removeLee.click();
  await d.getByRole("button", { name: "Remove" }).click();
  await card.locator("tr", { hasText: "lee@contoso.com" }).waitFor({ state: "detached" });
  assert.deepStrictEqual(sent("platform_remove_admin"), [{ user: "lee@contoso.com" }]);
  // A team member sees the admins with no buttons.
  me = { admin: false, user: "lee@contoso.com" };
  await page().evaluate(() => render());
  await card.getByText("kim@contoso.com").waitFor();
  assert.strictEqual(await card.getByRole("button").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("A source's panel lists its newest files and readers; Edit saves it and Remove asks first", { skip }, async () => {
  over.platform_landing = () => ({ env: "dev", kind: "fabric", where: "Platform Dev", central: false, system_types: ["SAP"], zones: [],
    sources: [{ name: "sap", code: "SAP", title: "SAP ERP", registered: true, source_id: 7, system_type: "SAP", owner: "erp-team", description: "Orders and customers.",
      expected_hours: 24, entities: [{ name: "orders", files: 3, size: 2048, newest: at }], readers: [{ project: "sales", entities: ["orders"] }],
      late: true, newest: at, connections: { prod: { secret_name: "sap-prod" } } }] });  // pragma: allowlist secret (a Key Vault secret's name)
  over.platform_landing_files = () => [{ path: "sap/orders/2026-11-18.csv", size: 1024, modified: at }];
  await page().evaluate(() => goPlatform("landing"));
  const panel = page().locator(".side-panel");
  await panel.getByText("sap/orders/2026-11-18.csv").waitFor();
  const text = await panel.innerText();
  assert.match(text, /SAP · id 7 · SAP/);
  assert.match(text, /Orders and customers\. Owner erp-team\./);
  assert.match(text, /orders\s+3\s+2(\.0)? KB/);
  assert.match(text, /sales\s+orders/);
  assert.match(text, /landing = "sap\/orders\/\*\.csv"/);
  assert.deepStrictEqual(sent("platform_landing_files"), [{ env: "dev", source: "sap", limit: 8 }]);
  await panel.getByRole("button", { name: "Edit" }).click();
  const d = dialog("Edit sap");
  assert.strictEqual(await d.locator("input").first().isDisabled(), true, "a registered source keeps its folder name");
  await d.locator("label", { hasText: "Owner" }).locator("input").fill("sap-team");
  await d.locator("label", { hasText: "Dev" }).locator("input").fill("sap-dev");
  await d.getByRole("button", { name: "Save" }).click();
  await d.waitFor({ state: "detached" });
  await until(() => sent("platform_save_source").length === 1, "the save");
  const saved = sent("platform_save_source")[0].source;
  assert.deepStrictEqual([saved.name, saved.owner, saved.system_type, saved.code, saved.active, saved.connections],
    ["sap", "sap-team", "SAP", "SAP", true, { dev: { secret_name: "sap-dev" }, prod: { secret_name: "sap-prod" } }]);  // pragma: allowlist secret
  await panel.getByRole("button", { name: "Remove" }).click();
  const sure = dialog("Remove source sap?");
  assert.match(await sure.innerText(), /Only the registration goes; files in the landing zone stay\./);
  await sure.getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(sent("platform_delete_source").length, 0);
  await panel.getByRole("button", { name: "Remove" }).click();
  await sure.getByRole("button", { name: "Remove" }).click();
  await until(() => sent("platform_delete_source").length === 1, "the removal");
  assert.deepStrictEqual(sent("platform_delete_source"), [{ name: "sap" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Audit lists every change, newest first, and says when there's nothing", { skip }, async () => {
  over.platform_audit = () => [{ at, by_user: "ana@contoso.com", action: "create key table", target: "sk.store" }, { at, by_user: null, action: "add admin", target: "kim" }];
  await page().evaluate(() => goPlatform("audit"));
  const card = page().locator(".card", { hasText: "Every change in the catalog, newest first" });
  await card.getByText("sk.store").waitFor();
  assert.match(await card.innerText(), /ana@contoso\.com\s+create key table\s+sk\.store/);
  assert.match(await card.innerText(), /someone\s+add admin\s+kim/);
  assert.deepStrictEqual(sent("platform_audit"), [{ limit: 200 }]);
  over.platform_audit = () => [];
  await page().evaluate(() => render());
  await card.getByText("Nothing yet.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

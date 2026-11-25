// Entra ID groups per domain (entra-groups.js), the paths entra-groups.test leaves out: someone who
// can't link or rename (the dialog says who can, and Naming opens read-only), a linked group that
// Check in Entra can't find, Unlink, Copy, the groups or the script failing to load, a search that
// finds nothing or fails, lower-case naming, and the Access to the domain card on a data product.
// The platform's replies are mocked; what the person sees and what was sent is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const GID = "7c1e2f3a-1111-2222-3333-44445555a90d";
const DOMAIN = { id: "sales", name: "Sales" };
const db = {};
const naming = () => ({ pattern: "{PREFIX}-{DOMAIN}-{ROLE}-{ENV}", prefix: "sg", case: db.case, domain_part: "name",
  words: { domain_admins: "Owners", readers: "Readers", creators: "Creators" }, roles_off: [] });
const row = (role, label, env, name, linked) => ({ domain_id: "sales", domain: "Sales", role, label, env, suggested: name,
  who: "Someone", gets: "Something", linked: linked ? { group_id: GID, group_name: name } : null, name });
const groups = () => {
  if (db.groupsFail) throw new Error(db.groupsFail);
  return { domain: DOMAIN, naming: naming(), can_link: db.canLink, can_name: db.canLink, rows: [
    row("domain_admins", "Domain owners", "", "SG-SALES-OWNERS", false),
    row("readers", "Data readers, Prod", "prod", "Sales Analytics Readers", db.linked),
    row("viewers", "Report viewers, Prod", "prod", "SG-SALES-VIEWERS-PROD", false)] };
};

let app;
const page = () => app.page;
const dialog = (name) => page().getByRole("dialog", { name });
const sent = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_entra_groups: groups,
    platform_entra_script: ({ kind }) => { if (db.scriptFail) throw new Error(db.scriptFail); return { text: `# ${kind} script`, steps: [] }; },
    platform_entra_check: () => ({ status: { "sales|readers|prod": { exists: false }, "sales|domain_admins|": { exists: true } } }),
    platform_entra_unlink: () => { db.linked = false; return { unlinked: true }; },
    platform_entra_find: ({ query }) => { if (/fail/.test(query)) throw new Error("Graph said: Insufficient privileges to complete the operation."); return []; },
  } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(db, { canLink: true, linked: true, case: "upper", groupsFail: null, scriptFail: null });
  await page().evaluate(() => {
    dialogOnClose = null; closeDialog(); closeDialog();
    window.__copied = null;
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async (t) => { window.__copied = t; } }, configurable: true });
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    document.getElementById("t-box")?.remove();
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

const open = async () => {
  await page().evaluate((d) => { entraGroupsDialog(d); }, DOMAIN);
  const d = dialog("Access groups for Sales");
  await d.locator('tr[data-role="readers"]').waitFor();
  return d;
};

test("someone who can't link is told who can; Naming opens read-only, in lower case, and Close comes back", { skip }, async () => {
  db.canLink = false;
  db.case = "lower";
  const d = await open();
  assert.match(await d.innerText(), /Only platform admins or Sales's owners can link groups they already have\./);
  assert.strictEqual(await d.getByRole("button", { name: /Link existing group|Unlink/ }).count(), 0);
  const namingBtn = d.getByRole("button", { name: /^Naming: / });
  assert.strictEqual(await namingBtn.getAttribute("title"), "How groups are named. Platform admins change it.");
  await namingBtn.click();
  const n = dialog("Group naming");
  await n.getByText("Only platform admins can change it.").waitFor();
  assert.ok(await n.getByRole("textbox").first().isDisabled());
  assert.deepStrictEqual(await n.locator(".entra-preview div").allInnerTexts(), ["sg-finance-owners", "sg-finance-creators-dev", "sg-finance-readers-prod"]);
  assert.strictEqual(await n.getByRole("button", { name: "Save" }).count(), 0);
  await n.locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  await dialog("Access groups for Sales").locator('tr[data-role="readers"]').waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Check in Entra flags a linked group it can't find; Unlink unlinks it; Copy copies the script", { skip }, async () => {
  const d = await open();
  await d.getByRole("button", { name: "Check in Entra" }).click();
  await d.locator('tr[data-role="readers"]').getByText("Linked, but not found in Entra").waitFor();
  await d.locator('tr[data-role="domain_admins"]').getByText("Already in Entra").waitFor();
  await page().waitForFunction(() => document.querySelector(".entra-script")?.textContent === "# powershell script");
  await d.getByRole("button", { name: "Copy", exact: true }).click();
  await toastSays(/^Script copied$/);
  assert.strictEqual(await page().evaluate(() => window.__copied), "# powershell script");
  await d.locator('tr[data-role="readers"]').getByRole("button", { name: "Unlink" }).click();
  await d.locator('tr[data-role="readers"]').getByRole("button", { name: "Link existing group" }).waitFor();
  assert.deepStrictEqual(sent("platform_entra_unlink"), [{ domain_id: "sales", role: "readers", env: "prod" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("groups that can't be read say why; a script that can't be written says why in its place", { skip }, async () => {
  db.groupsFail = "The platform's catalog couldn't be reached.";
  await page().evaluate((d) => { entraGroupsDialog(d); }, DOMAIN);
  const d = dialog("Access groups for Sales");
  await d.locator(".entra-table .fail-text").getByText(/catalog couldn't be reached/).waitFor();
  db.groupsFail = null;
  db.scriptFail = "The naming pattern has no {DOMAIN}.";
  await d.getByRole("checkbox", { name: "Dev" }).uncheck();
  await d.locator('tr[data-role="readers"]').waitFor();
  await page().waitForFunction(() => /no \{DOMAIN\}/.test(document.querySelector(".entra-script")?.textContent));
  assert.deepStrictEqual(sent("platform_entra_groups").at(-1), { domain_id: "sales", envs: ["test", "prod"], subdomains: false });
  assert.deepStrictEqual(app.errors, []);
});

test("a search that finds nothing says so; one Entra refuses says why", { skip }, async () => {
  db.linked = false;
  const d = await open();
  await d.locator('tr[data-role="readers"]').getByRole("button", { name: "Link existing group" }).click();
  const l = dialog("Link a group to Data readers, Prod");
  await l.getByRole("textbox", { name: "Find a group" }).fill("nomatch");
  await l.getByText("No group has that in its name.").waitFor();
  await l.getByRole("textbox", { name: "Find a group" }).fill("fail");
  await l.locator(".warn-box").getByText(/Insufficient privileges/).waitFor();
  assert.strictEqual(await l.locator(".entra-results").innerText(), "");
  assert.deepStrictEqual(app.errors, []);
});

test("a data product's Access card lists the groups to ask for and copies a name; no platform, no card", { skip }, async () => {
  const mount = (p) => page().evaluate((p) => {
    const b = document.createElement("div"); b.id = "t-box"; document.body.append(b);
    const card = productDomainGroupsCard(p);
    if (card) b.append(card);
    return !!card;
  }, p);
  assert.strictEqual(await mount({ name: "Orders" }), false, "a product with no domain has no card");
  await page().evaluate(() => document.getElementById("t-box").remove());
  assert.strictEqual(await mount({ name: "Orders", domain_id: "sales" }), true);
  const card = page().locator("#t-box .product-groups");
  await card.getByText("Access to Sales").waitFor();
  assert.deepStrictEqual((await card.locator(".row.tight .entra-ask").allInnerTexts()), ["Read the data", "See the reports", "Owners"]);
  assert.deepStrictEqual(await card.locator("code").allInnerTexts(), ["Sales Analytics Readers", "SG-SALES-VIEWERS-PROD", "SG-SALES-OWNERS"]);
  await card.getByRole("button", { name: "Copy SG-SALES-VIEWERS-PROD" }).click();
  await toastSays(/^Group name copied$/);
  assert.strictEqual(await page().evaluate(() => window.__copied), "SG-SALES-VIEWERS-PROD");
  assert.deepStrictEqual(sent("platform_entra_groups"), [{ domain_id: "sales", envs: ["prod"] }]);
  await page().evaluate(() => document.getElementById("t-box").remove());
  db.groupsFail = "No platform";
  await mount({ name: "Orders", domain_id: "sales" });
  await page().waitForFunction(() => !document.querySelector("#t-box .product-groups"));
  assert.deepStrictEqual(app.errors, []);
});

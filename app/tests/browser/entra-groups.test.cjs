// Entra ID groups per domain: Platform > Set up > Domains > Access groups suggests a group per role, links a
// group someone already has, and shows the scripts (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const GID = "7c1e2f3a-1111-2222-3333-44445555a90d";
const calls = [];
let linked = null;
const naming = { pattern: "{PREFIX}-{DOMAIN}-{SUBDOMAIN}-{ROLE}-{ENV}", prefix: "SG-FAB", case: "upper", domain_part: "id",
  words: { domain_admins: "DOMAIN-ADMINS", domain_contributors: "DOMAIN-CONTRIBUTORS", workspace_admins: "WSADMINS", creators: "CREATORS",
    readers: "READERS", viewers: "VIEWERS", stewards: "STEWARDS" }, roles_off: [] };
const row = (role, label, env, suggested) => {
  const link = linked && linked.role === role && linked.env === env ? linked : null;
  return { domain_id: "sales", domain: "Sales", role, label, env, suggested, mail_nickname: suggested.toLowerCase(), who: "Someone",
    gets: "Something", linked: link ? { group_id: link.group_id, group_name: link.group_name } : null, name: link ? link.group_name : suggested };
};
const groups = () => ({ domain: { id: "sales", name: "Sales" }, naming, owners: ["ana@contoso.com"], can_link: true, can_name: true,
  rows: [row("domain_admins", "Domain owners", "", "SG-FAB-SALES-DOMAIN-ADMINS"), row("readers", "Data readers, Prod", "prod", "SG-FAB-SALES-READERS-PROD")] });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { platform_whoami: () => ({ admin: true }), readiness_check: () => [],
    platform_domains: () => [{ id: "sales", name: "Sales", owners: "ana@contoso.com" }],
    platform_entra_groups: groups,
    platform_entra_script: ({ kind }) => ({ kind, text: `# ${kind} script\n${linked ? "" : "SG-FAB-SALES-READERS-PROD\n"}`, new: 1,
      steps: kind === "csv" ? ["1. In the Microsoft Entra admin center, open Groups."] : [] }),
    platform_entra_find: ({ query }) => (/sales/i.test(query) ? [
      { id: GID, name: "Sales Analytics Readers", kind: "Security group", usable: true },
      { id: "00000000-0000-0000-0000-000000000009", name: "sales-dl", kind: "Distribution list", usable: false }] : []),
    platform_entra_link: (p) => { calls.push(["link", p]); linked = { role: p.role, env: p.env, group_id: p.group_id, group_name: p.group_name }; return { linked: [] }; },
    platform_entra_naming_save: (p) => { calls.push(["naming", p]); return p.naming; },
  } });
});
after(async () => { if (app) await app.close(); });

const openGroups = async () => {
  await page().evaluate(() => { dialogOnClose = null; closeDialog(); state.user = "chris@contoso.com"; goPlatform("domains"); });
  await page().click('button:has-text("Access groups")');
  await page().waitForSelector('[role=dialog] tr[data-role="readers"]');
};

test("Access groups lists a group per role and the script that creates the new ones", { skip }, async () => {
  await openGroups();
  const text = await page().textContent("[role=dialog]");
  assert.match(text, /SG-FAB-SALES-DOMAIN-ADMINS/);
  assert.match(text, /Naming: \{PREFIX\}-\{DOMAIN\}/);
  await page().waitForFunction(() => /powershell script/.test(document.querySelector(".entra-script").textContent));
  await page().click('[role=dialog] .seg button[data-kind="csv"]');
  await page().waitForFunction(() => /csv script/.test(document.querySelector(".entra-script").textContent));
  assert.match(await page().textContent(".entra-steps"), /Entra admin center/);
  assert.match(await page().textContent("[role=dialog] .how-it-works"), /Nothing here creates a group in Entra/);
});

test("Link existing group finds a group, skips a distribution list, and comes back linked", { skip }, async () => {
  await openGroups();
  await page().click('tr[data-role="readers"] button:has-text("Link existing group")');
  await page().fill("[role=dialog] label:has-text('Find a group') input", "sales");
  await page().waitForSelector(".entra-result");
  assert.ok(await page().isDisabled('.entra-result:has-text("sales-dl") input'));
  await page().check('.entra-result:has-text("Sales Analytics Readers") input');
  await page().click('[role=dialog] button:has-text("Link group")');
  await page().waitForSelector('[role=dialog] tr[data-role="readers"] >> text="Sales Analytics Readers"');
  const [kind, p] = calls.pop();
  assert.strictEqual(kind, "link");
  assert.deepStrictEqual({ ...p }, { domain_id: "sales", role: "readers", env: "prod", group_id: GID, group_name: "Sales Analytics Readers", envs: null });
  assert.ok(await page().$('tr[data-role="readers"] button:has-text("Unlink")'));
});

test("Naming previews the names and saves the pattern for every domain", { skip }, async () => {
  await openGroups();
  await page().click('[role=dialog] button:has-text("Naming:")');
  await page().fill("[role=dialog] label:has-text('Prefix') input", "GRP");
  assert.match(await page().textContent(".entra-preview"), /GRP-FINANCE-READERS-PROD/);
  assert.match(await page().textContent(".entra-preview"), /GRP-FINANCE-DOMAIN-ADMINS(?!-)/);
  await page().click('[role=dialog] button:has-text("Save")');
  await page().waitForSelector('[role=dialog] tr[data-role="readers"]');
  const [kind, p] = calls.pop();
  assert.strictEqual(kind, "naming");
  assert.strictEqual(p.naming.prefix, "GRP");
});

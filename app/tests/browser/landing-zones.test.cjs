// One central landing workspace (Preview): the Landing zone tab without the environment picker, its
// zone cards and Zone column, the New landing zone dialog, and the one-time move (see harness.cjs).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
let central = false;

const zones = [
  { id: "shared", name: "Shared", lakehouse_name: "LH_Landing", secure: false, readers: [], label: "", devtest: "read", domain_id: null, sources: ["sap"] },
  { id: "hr", name: "HR", lakehouse_name: "LH_Landing_HR", secure: false, readers: ["HR Engineers"], label: "", devtest: "read", domain_id: "hr", sources: ["workday"] },
  { id: "hr_secure", name: "HR-Secure", lakehouse_name: "LH_Landing_HR_Secure", secure: true, readers: ["HR Secure"], label: "Highly Confidential",
    devtest: "none", domain_id: "hr", sources: ["payroll"] },
];
const source = (name, zone, zoneName, extra = {}) => ({ name, registered: true, title: name, code: name.toUpperCase(), source_id: 1, system_type: "",
  active: true, description: "", owner: "", expected_hours: "24", readers: [], connections: {}, entities: [], newest: "", late: false,
  zone, zone_name: zoneName, ...extra });
const landing = (p) => (central
  ? { env: p.env, central: true, kind: "fabric", where: "Platform - Landing", landing: { workspace_id: "ws-l", workspace_name: "Platform - Landing" },
    zones, policy: false, default_landing_name: "Platform - Landing", problem: null, system_types: ["SAP"],
    sources: [source("sap", "", "Shared", { late: true }), source("workday", "hr", "HR"), source("payroll", "hr_secure", "HR-Secure")] }
  : { env: p.env, central: false, kind: "local", where: "C:\\platform\\landing-dev\\Files", landing: {}, zones: [], policy: false,
    default_landing_name: "Platform - Landing", problem: null, system_types: ["SAP"], sources: [source("sap", "", "")] });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { platform_whoami: () => ({ admin: true }), platform_landing: landing, platform_landing_files: () => [],
    readiness_check: () => [], list_capacities: () => [{ id: "cap1", displayName: "F64", sku: "F64" }],
    platform_domains: () => [{ id: "hr", name: "HR" }],
    platform_landing_setup: () => { central = true; return { ready: true, steps: [{ step: "Landing workspace", status: "done", detail: "Created Platform - Landing" }] }; },
    platform_create_landing_zone: () => ({ created: true, lakehouse: "LH_Landing_Finance_Secure", zones }),
    platform_save_source: () => [] } });
});
after(async () => { if (app) await app.close(); });

const open = async () => {
  await page().evaluate(() => { state.user = "chris@contoso.com"; goPlatform("landing"); });
  await page().waitForFunction(() => platformPage.me);
};
const headPicker = () => page().$('.page-head [aria-label="Environment"]');
const calls = (method) => app.calls.filter((c) => c.method === method);

test("without one landing workspace the tab keeps its environment picker and offers the move", { skip }, async () => {
  await open();
  await page().waitForSelector('text="Move to one landing workspace"');
  assert.ok(await headPicker());
  assert.match(await page().textContent(".callout"), /Preview/);
});

test("the move says what it creates and that files aren't copied, and asks before creating", { skip }, async () => {
  await page().click('text="Move to one landing workspace"');
  await page().waitForSelector('[role=dialog] >> text="Move"');
  const text = await page().textContent("[role=dialog]");
  assert.match(text, /aren't copied yet: they stay where they are/);
  assert.match(text, /Nothing is deleted/);
  assert.strictEqual(await page().inputValue("[role=dialog] input"), "Platform - Landing");
  await page().click('[role=dialog] button:text-is("Move")');
  await page().waitForSelector("text=Create Platform - Landing?");
  assert.match(await page().textContent("[role=dialog]"), /creates the workspace Platform - Landing in Fabric[\s\S]*LH_Landing/);
  assert.strictEqual(calls("platform_landing_setup").length, 0, "nothing is created before the confirm");
  await page().click('[role=dialog] button:text-is("Create")');
  await page().waitForSelector("text=Created Platform - Landing");
  assert.deepStrictEqual(calls("platform_landing_setup")[0].params, { workspace_name: "Platform - Landing", capacity_id: "cap1" });
  await page().click('[role=dialog] button:text-is("Close")');
});

test("with one landing workspace there's no environment picker, and zone cards and a Zone column show", { skip }, async () => {
  await open();
  await page().waitForSelector(".zone-card");
  await page().waitForFunction(() => !document.querySelector('.page-head [aria-label="Environment"]'));
  assert.strictEqual(await headPicker(), null);
  const cards = await page().$$eval(".zone-card", (cs) => cs.map((c) => c.textContent));
  assert.strictEqual(cards.length, 3);
  assert.match(cards[2], /HR-Secure[\s\S]*Secure[\s\S]*Readers: HR Secure[\s\S]*Label: Highly Confidential[\s\S]*Prod reads it\. Dev and Test don't\./);
  assert.match(cards[2], /Set in Fabric for now: a OneLake data access role on LH_Landing_HR_Secure/);
  assert.match(cards[0], /Dev, Test and Prod read it/);
  const heads = await page().$$eval("table.list thead th", (ths) => ths.map((t) => t.textContent));
  assert.ok(heads.includes("Zone"));
  assert.match(await page().textContent("table.list tbody"), /HR-Secure/);
  assert.match(await page().textContent(".how-it-works"), /LH_Landing_<Name>/);
});

test("a source's edit dialog picks its zone", { skip }, async () => {
  await page().click('tr:has-text("WORKDAY")');
  await page().click('.side-panel button:text-is("Edit")');
  await page().waitForSelector("text=Edit workday");
  const zone = page().locator('[role=dialog] label:has-text("Zone") select');
  assert.strictEqual(await zone.inputValue(), "hr");
  await zone.selectOption("hr_secure");
  await page().click('[role=dialog] button:text-is("Save")');
  await page().waitForFunction(() => !document.querySelector("[role=dialog]"));
  assert.strictEqual(calls("platform_save_source").at(-1).params.source.zone, "hr_secure");
});

test("New landing zone asks for the board's fields, defaults to Prod only, and confirms before creating", { skip }, async () => {
  await page().click('button:text-is("New landing zone")');
  await page().waitForSelector('[role=dialog] >> text="Create zone"');
  const dialog = page().locator("[role=dialog]");
  for (const label of ["Name", "Domain", "Who reads it", "Sensitivity label", "Don't read it (Prod only)", "Read it like Prod"]) {
    assert.ok(await dialog.locator(`text=${label}`).count(), label);
  }
  assert.ok(await dialog.locator('input[type=radio]').first().isChecked());
  await dialog.locator('label:has-text("Name") input').fill("Finance-Secure");
  await dialog.locator('input[type=checkbox]').check();
  assert.match(await dialog.textContent(), /Creates LH_Landing_Finance_Secure in Platform - Landing/);
  assert.match(await dialog.textContent(), /set them on LH_Landing_Finance_Secure in Fabric/);
  await page().click('[role=dialog] button:text-is("Create zone")');
  await page().waitForSelector("text=Create the landing zone Finance-Secure?");
  assert.strictEqual(calls("platform_create_landing_zone").length, 0);
  await page().click('[role=dialog] button:text-is("Create zone")');
  await page().waitForFunction(() => !document.querySelector("[role=dialog]"));
  const [call] = calls("platform_create_landing_zone");
  assert.deepStrictEqual({ ...call.params.zone, readers: undefined }, { name: "Finance-Secure", domain_id: "", readers: undefined,
    secure: true, label: "", devtest: "none" });
});

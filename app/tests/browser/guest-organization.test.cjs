// Guest (B2B) sign-in (settings.js switchOrganizationDialog, organizationRow; app.js signInCallout and
// renderChrome; organizations_service.py). "Guest of another company?" on the sign-in prompt and Switch
// organization in Settings > Account open the same dialog: own organization, recent ones and another
// one, whose domain or tenant id is checked before sign-in. A domain Microsoft doesn't know says so and
// keeps Sign in off; a known one names it, and Sign in sends organization_switch once with its tenant
// id. Signed in as a guest, the app bar shows a Guest tag and Settings says whose workspaces these are.
// When IT fixed the organization, Settings says IT chose it and there's no Switch button. axe finds
// nothing serious in the dialog. Settings come from the real engine (app_settings.py); the organization
// calls are mocked in the shapes organizations_service.py returns.
// axe covers dialogs: settings.js:switchOrganizationDialog
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const ME = "chris@contoso.com";
const HOME = "bbbbbbbb-1111-2222-3333-444444444444";
const HOST = "aaaaaaaa-1111-2222-3333-444444444444";
const NORTHWIND = { tenant_id: HOST, domain: "northwind.com", name: "northwind.com" };

const org = { fixed: false, guest: false, setting: "" };  // what the engine has, per test
const status = () => ({
  setting: org.setting, fixed: org.fixed, current: org.guest || org.fixed ? NORTHWIND : null,
  user: org.user, tenant: org.guest ? HOST : HOME, home_tenant: HOME, home_domain: "contoso.com",
  guest: org.guest, recent: org.recent || [],
});
const mocks = {
  readiness_check: () => [],
  list_workspaces: () => [],
  organization_status: () => status(),
  // organizations_service.organization_check: Microsoft's public metadata for one tenant
  organization_check: ({ text }) => {
    if (text.toLowerCase() === "northwind.com") return NORTHWIND;
    throw new Error(`ValueError: Microsoft has no organization at ${text}. Check the spelling, or ask the company for its tenant id.`);
  },
  organization_switch: ({ tenant }) => {
    org.guest = tenant === HOST;
    org.user = ME;
    return status();
  },
};

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings"], mocks });
  await startupReads(app);
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(org, { fixed: false, guest: false, setting: "", user: null, recent: [] });
  await page().evaluate(() => {
    closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    Object.assign(state, { user: null, tenant: null, guest: false, organization: "" }); renderChrome(); go("home");
  });
});

const calls = (from, method) => app.calls.slice(from).filter((c) => c.method === method).map((c) => c.params);
const account = () => page().locator(".page.settings section", { has: page().locator("h2", { hasText: "Account" }) });

async function openSettings() {
  await page().evaluate(() => go("settings"));
  await page().waitForFunction(() => /Organization/.test(document.querySelector(".page.settings")?.textContent || ""));
}

test("Guest of another company? checks the domain first, then signs in to it as a guest", { skip }, async () => {
  await page().evaluate(() => go("workspaces"));
  const from = app.calls.length;
  await page().getByRole("button", { name: "Guest of another company?" }).click();
  const dialog = page().getByRole("dialog", { name: "Switch organization" });
  await dialog.waitFor();
  assert.match(await dialog.innerText(), /Your account stays the same/);
  await dialog.getByRole("radio", { name: /Another organization/ }).check();
  const go = dialog.getByRole("button", { name: /^Sign in/ });
  assert.ok(await go.isDisabled(), "nothing checked yet");

  const field = dialog.getByRole("textbox", { name: "Its domain or tenant id" });
  await field.fill("northwind.co");
  await dialog.getByRole("button", { name: "Check" }).click();
  await dialog.getByRole("alert").filter({ hasText: "Microsoft has no organization at northwind.co" }).waitFor();
  assert.doesNotMatch(await dialog.innerText(), /ValueError|Error:/);
  assert.strictEqual(await field.getAttribute("aria-invalid"), "true");
  assert.ok(await go.isDisabled());
  // and it looks off, not like a button that works (UX review)
  const look = await go.evaluate((b) => getComputedStyle(b).cursor);
  assert.strictEqual(look, "not-allowed");

  await field.fill("northwind.com");
  await field.press("Enter");
  await dialog.getByText("Found northwind.com. Microsoft asks you to sign in next.").waitFor();
  assert.strictEqual(await go.innerText(), "Sign in to northwind.com");
  await field.fill("northwind.comx");
  assert.ok(await go.isDisabled(), "editing the field needs a new check");
  await field.fill("northwind.com");
  await field.press("Enter");
  await dialog.getByText("Found northwind.com. Microsoft asks you to sign in next.").waitFor();
  assert.deepStrictEqual(calls(from, "organization_switch"), [], "checking signs nobody in");
  await go.click();
  await dialog.waitFor({ state: "detached" });
  await page().waitForFunction(() => state.guest === true);
  assert.deepStrictEqual(calls(from, "organization_switch"), [{ tenant: HOST, domain: "northwind.com" }]);
  const tag = page().locator("#account-guest");
  assert.strictEqual(await tag.innerText(), "Guest");
  assert.match(await page().locator("#account").getAttribute("aria-label"), /guest in northwind\.com/);

  await openSettings();
  const text = await account().innerText();
  assert.match(text, /Guest in northwind\.com\. Your home organization is contoso\.com/);
  assert.match(text, /Workspaces, lakehouses and runs in Fabric now come from northwind\.com/);
  assert.deepStrictEqual(app.errors, []);
});

test("Switch organization in Settings goes back to your own organization, and recent ones are offered", { skip }, async () => {
  Object.assign(org, { guest: true, user: ME, setting: HOST, recent: [{ ...NORTHWIND, used: Date.now() / 1000 - 3600 }] });
  await page().evaluate((me) => Object.assign(state, { user: me, guest: true, organization: "northwind.com" }), ME);
  await openSettings();
  const from = app.calls.length;
  await account().getByRole("button", { name: "Switch organization" }).click();
  const dialog = page().getByRole("dialog", { name: "Switch organization" });
  await dialog.waitFor();
  assert.ok(await dialog.getByRole("radio", { name: /northwind\.com/ }).isChecked());
  await dialog.getByRole("radio", { name: /Your own organization/ }).check();
  await dialog.getByRole("button", { name: "Sign in to your own organization (contoso.com)" }).click();
  await page().waitForFunction(() => state.guest === false);
  assert.deepStrictEqual(calls(from, "organization_switch"), [{ tenant: "", domain: "" }]);
  assert.strictEqual(await page().locator("#account-guest").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("an organization IT chose says so, with no Switch button", { skip }, async () => {
  Object.assign(org, { fixed: true, guest: true, user: ME, setting: "northwind.com" });
  await page().evaluate((me) => Object.assign(state, { user: me, guest: true, organization: "northwind.com" }), ME);
  await openSettings();
  const text = await account().innerText();
  assert.match(text, /IT chose this organization for sign-in\. To change it, ask IT/);
  assert.match(text, /Set by your organization's policy/);
  assert.strictEqual(await account().getByRole("button", { name: "Switch organization" }).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("axe finds nothing serious in Switch organization", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await page().evaluate(() => switchOrganizationDialog());
  const dialog = page().getByRole("dialog", { name: "Switch organization" });
  await dialog.waitFor();
  await dialog.getByRole("radio", { name: /Another organization/ }).check();
  assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), []);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});

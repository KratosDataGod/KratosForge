// Data mesh Wave 2, P6: the change window card in Platform > Govern > Governance. Admins set or clear
// it; everyone else reads it (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
let admin = true;
let current = { available: true, window: null };
let readFails = false;
const saves = [];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_whoami: () => ({ admin, user: "ana@contoso.com", domains_owned: [] }),
    governance_overview: () => ({ me: { admin, domains_owned: [] },
      options: { categories: {}, enforcements: ["advisory", "warn", "block"], severities: ["low"] },
      policies: [], counts: {}, domains: [], projects: [], exceptions: [], problems: [] }),
    platform_change_window: () => { if (readFails) throw new Error("network is unreachable"); return current; },
    platform_save_change_window: ({ window }) => { saves.push(window); current = { available: true, window }; return { window }; },
  } });
});
after(async () => { if (app) await app.close(); });

const openGovern = async (selector = ".change-window .cw-now, .change-window .wrn-text") => {
  await page().evaluate(() => { govPage.view = "policies"; govPage.open = null; goPlatform("governance"); });
  await page().waitForSelector(selector);
};

test("with no window an admin sets one, and the card says it in words", { skip }, async () => {
  admin = true;
  current = { available: true, window: null };
  await openGovern();
  assert.match(await page().textContent(".change-window"), /None set/);
  await page().click('.change-window button:has-text("Set a change window")');
  await page().waitForSelector(".cw-days");
  await page().uncheck('.cw-days label:has-text("Thu") input');
  await page().fill('.dialog label.field:has-text("From") input', "09:00");
  await page().fill('.dialog label.field:has-text("Until") input', "17:30");
  await page().fill('.dialog label.field:has-text("Time zone") input', "Europe/London");
  await page().selectOption('.dialog label.field:has-text("Applies to") select', "test,prod");
  await page().click('.dialog button:has-text("Save")');
  await page().waitForFunction(() => /Mon to Wed/.test(document.querySelector(".change-window .cw-now")?.textContent || ""));
  assert.deepStrictEqual(saves.at(-1), { days: ["mon", "tue", "wed"], start: "09:00", end: "17:30", time_zone: "Europe/London", envs: ["test", "prod"] });
  assert.strictEqual(await page().textContent(".change-window .cw-now"), "Mon to Wed, 09:00 to 17:30 (Europe/London), for Test and Prod");
});

test("an admin changes or clears the window", { skip }, async () => {
  admin = true;
  current = { available: true, window: { days: ["mon", "fri"], start: "08:00", end: "16:00", time_zone: "UTC", envs: ["prod"] } };
  await openGovern();
  assert.strictEqual(await page().textContent(".change-window .cw-now"), "Mon, Fri, 08:00 to 16:00 (UTC), for Prod");
  await page().click('.change-window button:has-text("Change")');
  await page().waitForSelector(".cw-days");
  assert.ok(await page().isChecked('.cw-days label:has-text("Fri") input'));
  assert.ok(!(await page().isChecked('.cw-days label:has-text("Tue") input')));
  await page().click('.dialog button:has-text("Clear")');
  await page().waitForSelector('.dialog:has-text("Clear the change window?")');
  assert.strictEqual(saves.length, 1, "nothing is cleared before the confirm");
  await page().click('.dialog button.danger-solid:has-text("Clear")');
  await page().waitForFunction(() => /None set/.test(document.querySelector(".change-window")?.textContent || ""));
  assert.strictEqual(saves.at(-1), null);
});

test("changing only the times keeps a window that covers Test alone", { skip }, async () => {
  admin = true;
  current = { available: true, window: { days: ["mon"], start: "08:00", end: "16:00", time_zone: "UTC", envs: ["test"] } };
  await openGovern();
  assert.match(await page().textContent(".change-window .cw-now"), /for Test$/);
  await page().click('.change-window button:has-text("Change")');
  await page().waitForSelector(".cw-days");
  await page().fill('.dialog label.field:has-text("Until") input', "18:00");
  await page().click('.dialog button:has-text("Save")');
  await page().waitForFunction(() => /18:00/.test(document.querySelector(".change-window .cw-now")?.textContent || ""));
  assert.deepStrictEqual(saves.at(-1), { days: ["mon"], start: "08:00", end: "18:00", time_zone: "UTC", envs: ["test"] });
});

test("someone who isn't an admin reads it with no buttons", { skip }, async () => {
  admin = false;
  current = { available: true, window: { days: ["mon", "tue", "wed", "thu"], start: "08:00", end: "16:00", time_zone: "UTC", envs: ["prod"] } };
  await openGovern();
  assert.strictEqual(await page().textContent(".change-window .cw-now"), "Mon to Thu, 08:00 to 16:00 (UTC), for Prod");
  assert.strictEqual(await page().$(".change-window button"), null);
});

test("a platform that can't keep a window says why and offers nothing", { skip }, async () => {
  admin = true;
  current = { available: false, window: null, problem: "Run Finish or repair on the platform first." };
  await openGovern();
  assert.match(await page().textContent(".change-window .wrn-text"), /Finish or repair/);
  assert.strictEqual(await page().$(".change-window button"), null);
});

test("with Preview features hidden the card isn't shown", { skip }, async () => {
  admin = true;
  current = { available: true, window: null };
  await page().evaluate(() => { state.settings = { ...(state.settings || {}), show_preview: { value: false } }; });
  await page().evaluate(() => { govPage.open = null; goPlatform("governance"); });
  await page().waitForSelector("text=No policies yet.");
  assert.strictEqual(await page().$(".change-window"), null);
  await page().evaluate(() => { state.settings.show_preview = { value: true }; });
});

test("the dialog says what's wrong next to the fields and saves nothing", { skip }, async () => {
  admin = true;
  current = { available: true, window: { days: ["mon"], start: "08:00", end: "16:00", time_zone: "UTC", envs: ["prod"] } };
  await openGovern();
  const before = saves.length;
  await page().click('.change-window button:has-text("Change")');
  await page().waitForSelector(".cw-days");
  assert.strictEqual(await page().textContent(".dialog fieldset.cw-days-set legend"), "Days");
  await page().fill('.dialog label.field:has-text("Until") input', "07:00");
  await page().click('.dialog button:has-text("Save")');
  assert.match(await page().textContent(".cw-problem"), /Until must be later than From/);
  await page().fill('.dialog label.field:has-text("Until") input', "17:00");
  await page().uncheck('.cw-days label:has-text("Mon") input');
  await page().click('.dialog button:has-text("Save")');
  assert.match(await page().textContent(".cw-problem"), /Tick at least one day/);
  assert.strictEqual(saves.length, before);
  await page().click('.dialog button:has-text("Cancel")');
});

test("a read that fails says so and offers Retry", { skip }, async () => {
  admin = true;
  readFails = true;
  await openGovern('.change-window button:has-text("Retry")');
  assert.match(await page().textContent(".change-window"), /Couldn't read the change window/);
  readFails = false;
  current = { available: true, window: null };
  await page().click('.change-window button:has-text("Retry")');
  await page().waitForSelector(".change-window .cw-now");
});

test("the card and the dialog pass axe", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  admin = true;
  current = { available: true, window: { days: ["mon"], start: "08:00", end: "16:00", time_zone: "UTC", envs: ["prod"] } };
  await openGovern();
  assert.deepStrictEqual(await axeFindings(page(), ".change-window"), []);
  await page().click('.change-window button:has-text("Change")');
  await page().waitForSelector(".cw-days");
  assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  await page().click('.dialog button:has-text("Cancel")');
});

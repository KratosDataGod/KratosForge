// Data mesh Wave 2, P6: the change window card in Platform > Govern > Governance. Admins set or clear
// it; everyone else reads it (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
let admin = true;
let current = { available: true, window: null };
const saves = [];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_whoami: () => ({ admin, user: "ana@contoso.com", domains_owned: [] }),
    governance_overview: () => ({ me: { admin, domains_owned: [] },
      options: { categories: {}, enforcements: ["advisory", "warn", "block"], severities: ["low"] },
      policies: [], counts: {}, domains: [], projects: [], exceptions: [], problems: [] }),
    platform_change_window: () => current,
    platform_save_change_window: ({ window }) => { saves.push(window); current = { available: true, window }; return { window }; },
  } });
});
after(async () => { if (app) await app.close(); });

const openGovern = async () => {
  await page().evaluate(() => { govPage.view = "policies"; govPage.open = null; goPlatform("governance"); });
  await page().waitForSelector(".change-window .cw-now, .change-window .wrn-text");
};

test("with no window an admin sets one, and the card says it in words", { skip }, async () => {
  admin = true;
  current = { available: true, window: null };
  await openGovern();
  assert.match(await page().textContent(".change-window"), /None set/);
  await page().click('.change-window button:has-text("Set a change window")');
  await page().waitForSelector(".cw-days");
  await page().uncheck('.cw-days input[aria-label="Thu"]');
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
  assert.ok(await page().isChecked('.cw-days input[aria-label="Fri"]'));
  assert.ok(!(await page().isChecked('.cw-days input[aria-label="Tue"]')));
  await page().click('.dialog button:has-text("Clear")');
  await page().waitForFunction(() => /None set/.test(document.querySelector(".change-window")?.textContent || ""));
  assert.strictEqual(saves.at(-1), null);
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

// Platform notices (Preview) against the real engine: routes set in Settings > Notifications,
// and the bell's Platform filter listing what's still open (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const HOOK = "https://127.0.0.1:9/webhookb2/abc?sig=SECRET123";  // refused at once: Teams "didn't answer"
const event = (kind, id, title, extra = {}) => ({ kind, id, title, at: "2026-10-21T08:00:00+00:00", domain: "finance", env: "prod",
  detail: kind.startsWith("shortcut.") ? "Its target folder no longer exists" : "Because I need the Q3 numbers",
  to: { roles: ["platform_admin"] }, open: kind.startsWith("shortcut.") ? { view: "shortcuts", tab: "health" } : { view: "platform", tab: "requests" }, ...extra });

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "save_app_settings", "platform_notify", "platform_notify_settings", "platform_notify_save", "platform_notify_open"],
    mocks: { readiness_check: () => [], platform_domains: () => [{ id: "finance", name: "Finance" }, { id: "sales", name: "Sales" }] },
  });
  await page().waitForFunction(() => typeof platformNoticesSection === "function" && !!state.settings);
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

test("adding a route shows only its host, and editing keeps the address", { skip }, async () => {
  await page().evaluate(() => go("settings"));
  await page().getByRole("button", { name: "Add a route" }).click();
  await page().fill("#pn-name", "Data platform admins");
  await page().fill("#pn-url", HOOK);
  await page().getByLabel("Request waiting on someone").check();
  await page().getByRole("button", { name: "Save", exact: true }).click();
  await page().waitForSelector(".pn-routes");
  const text = await page().textContent(".pn-settings");
  assert.ok(text.includes("Data platform admins") && text.includes("127.0.0.1") && text.includes("Set, address hidden"));
  assert.ok(!text.includes("SECRET123"));
  await page().getByRole("button", { name: "Edit" }).click();
  assert.strictEqual(await page().inputValue("#pn-url"), "");
  assert.ok(await page().locator("#pn-name").isDisabled());
  await page().getByLabel("Finance").check();
  await page().getByRole("button", { name: "Save", exact: true }).click();
  await page().waitForFunction(() => document.querySelector(".pn-routes")?.textContent.includes("finance"));
  const saved = await page().evaluate(() => rpc("platform_notify_settings", {}));
  assert.deepStrictEqual(saved.routes[0], { name: "Data platform admins", kinds: ["request.waiting"], domains: ["finance"], url_set: true, host: "127.0.0.1" });
});

test("quiet hours and exemptions save from the section", { skip }, async () => {
  await page().fill("#pn-quiet", "20:00-07:00");
  await page().press("#pn-quiet", "Tab");
  await page().waitForFunction(async () => (await rpc("platform_notify_settings", {})).quiet === "20:00-07:00");
  await page().getByLabel("Warnings (governance marks these)").check();
  await page().waitForFunction(async () => (await rpc("platform_notify_settings", {})).quiet_exempt.includes("warn"));
  await page().fill("#pn-quiet", "");
  await page().press("#pn-quiet", "Tab");
  await page().waitForFunction(async () => (await rpc("platform_notify_settings", {})).quiet === "");
});

test("the bell lists open notices, never a request's reason, and the ones Teams didn't take", { skip }, async () => {
  await page().evaluate(([a, b]) => rpc("platform_notify", { events: [a, b] }), [
    event("request.waiting", "r1", "Access to LH_Gold_Sales"),
    event("shortcut.broken", "s1", "Customers_Silver"),
  ]);
  await page().evaluate(() => go("settings"));
  await page().waitForFunction(() => document.querySelector(".pn-waiting-line"));
  if (SHOTS) await page().locator(".pn-settings").screenshot({ path: path.join(SHOTS, "platform-notices-settings.png") });
  await page().evaluate(() => toggleTray(true));
  await page().getByRole("button", { name: /^Platform \d/ }).click();
  const tray = await page().textContent(".tray");
  for (const part of ["Platform 1", "Access to LH_Gold_Sales", "waits on platform admins", "Open in Platform > Requests",
    "1 notice hasn't reached Teams", "Teams didn't answer. It waits on this PC", "Details", "as far as this PC's notices know"]) assert.ok(tray.includes(part), part);
  assert.ok(!tray.includes("Q3") && !tray.includes("SECRET123"));
  assert.ok(!tray.includes("Customers_Silver"), "no route takes shortcut notices, so it wasn't posted");
  if (SHOTS) await page().locator(".tray").screenshot({ path: path.join(SHOTS, "platform-notices-bell.png") });
  await page().getByRole("button", { name: "Open in Platform > Requests" }).click();
  assert.strictEqual(await page().evaluate(() => [state.view, platformPage.tab].join(":")), "platform:requests");
});

test("removing a route asks first", { skip }, async () => {
  await page().evaluate(() => go("settings"));
  await page().getByRole("button", { name: "Edit" }).click();
  await page().getByRole("button", { name: "Remove route" }).click();
  await page().getByRole("button", { name: "Remove", exact: true }).click();
  await page().waitForFunction(async () => (await rpc("platform_notify_settings", {})).routes.length === 0);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

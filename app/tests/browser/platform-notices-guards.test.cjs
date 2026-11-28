// Platform notices: the guard paths the main test file doesn't reach. Forgetting the company mail
// server asks first, in danger styling, and only a confirmed Forget sends the call; a failed one says
// so and changes nothing. Each "Open in ..." link on a notice lands on the page it names. Mocks only.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
let forgets = 0;
let forgetFails = false;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const MAIL = { set: true, host: "smtp.contoso.com", port: 587, security: "starttls", sender: "kratosforge@contoso.com", username: "", password_saved: false };

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      readiness_check: () => [],
      mail_server_forget: () => {
        forgets += 1;
        if (forgetFails) throw new Error("Windows Credential Manager is locked");
        return { set: false };
      },
    },
  });
  await page().waitForFunction(() => typeof mailServerDialog === "function" && typeof openPlatformNotice === "function");
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

// Opens the mail server dialog for a saved server; window.__done counts the section's redraws.
async function openMailDialog() {
  await page().evaluate((m) => { window.__done = 0; mailServerDialog(m, () => { window.__done += 1; }); }, MAIL);
  await page().locator(".dialog").getByRole("button", { name: "Forget server" }).click();
  await page().waitForFunction(() => document.querySelector(".dialog")?.textContent.includes("Forget the company mail server?"));
}

test("Forget server is styled as dangerous, and its question is a danger confirmation", { skip }, async () => {
  await page().evaluate((m) => mailServerDialog(m, () => {}), MAIL);
  const forget = page().locator(".dialog").getByRole("button", { name: "Forget server" });
  assert.match(await forget.getAttribute("class"), /\bdanger\b/);
  await forget.click();
  await page().waitForFunction(() => document.querySelector(".dialog")?.textContent.includes("Forget the company mail server?"));
  const text = await page().textContent(".dialog");
  assert.ok(text.includes("Email routes stop sending") && text.includes("Windows Credential Manager"), text);
  assert.match(await page().locator(".dialog").getByRole("button", { name: "Forget", exact: true }).getAttribute("class"), /danger-solid/);
  await page().locator(".dialog").getByRole("button", { name: "Cancel" }).click();
});

test("Cancel on the question sends nothing and redraws nothing", { skip }, async () => {
  forgets = 0;
  await openMailDialog();
  await page().locator(".dialog").getByRole("button", { name: "Cancel" }).click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.strictEqual(forgets, 0);
  assert.strictEqual(await page().evaluate(() => window.__done), 0);
});

test("a confirmed Forget sends the call once, says so, and redraws the section", { skip }, async () => {
  forgets = 0;
  forgetFails = false;
  await openMailDialog();
  await page().locator(".dialog").getByRole("button", { name: "Forget", exact: true }).click();
  await page().waitForFunction(() => window.__done === 1);
  assert.strictEqual(forgets, 1);
  await page().waitForFunction(() => document.getElementById("toast")?.textContent.includes("Forgot the mail server"));
});

test("a Forget the engine refuses shows the reason as an error and redraws nothing", { skip }, async () => {
  forgets = 0;
  forgetFails = true;
  await openMailDialog();
  await page().locator(".dialog").getByRole("button", { name: "Forget", exact: true }).click();
  await page().waitForFunction(() => document.getElementById("toast")?.textContent.includes("Not removed"));
  const toast = await page().textContent("#toast");
  assert.ok(toast.includes("Windows Credential Manager is locked"), toast);
  assert.strictEqual(forgets, 1);
  assert.strictEqual(await page().evaluate(() => window.__done), 0);
  forgetFails = false;
});

test("a broken shortcut's notice opens OneLake on the Shortcuts health view", { skip }, async () => {
  await page().evaluate(() => openPlatformNotice({ open: { view: "shortcuts", tab: "health" } }));
  await page().waitForFunction(() => state.view === "onelake");
  assert.deepStrictEqual(await page().evaluate(() => [catalogPage.tab, scPage.view]), ["shortcuts", "health"]);
});

test("a data product's notice opens the Data mesh catalog", { skip }, async () => {
  await page().evaluate(() => openPlatformNotice({ open: { view: "onelake" } }));
  await page().waitForFunction(() => state.view === "metadata" && state.meta.tab === "catalog");
});

test("an incident's notice opens Monitor on the active incidents", { skip }, async () => {
  await page().evaluate(() => { incPage.filter = "all"; openPlatformNotice({ open: { view: "incident" } }); });
  await page().waitForFunction(() => state.view === "monitor");
  assert.deepStrictEqual(await page().evaluate(() => [incPage.filter, monitorPage.panel]), ["active", "incidents"]);
});

test("a policy exception's notice opens that exception in Governance", { skip }, async () => {
  await page().evaluate(() => openPlatformNotice({ open: { view: "governance", target: "exc-42" } }));
  await page().waitForFunction(() => govPage.view === "policies");
  assert.strictEqual(await page().evaluate(() => govPage.open), "exc-42");
});

test("a request's notice opens Data mesh > Requests", { skip }, async () => {
  await page().evaluate(() => openPlatformNotice({ open: { view: "platform", tab: "requests" } }));
  await page().waitForFunction(() => state.view === "metadata" && state.meta.tab === "requests");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

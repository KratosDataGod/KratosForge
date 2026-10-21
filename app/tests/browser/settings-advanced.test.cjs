// UX-006: Settings keeps the App (client) ID and engine limits in a folded Advanced section, and a
// value the environment or the policy file sets is read-only and says which. Runs the real engine
// (see harness.cjs for how to run it).
const { test, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const APP = "11111111-2222-3333-4444-555555555555";
let app;
after(async () => { await app?.close(); });

test("Advanced is folded, and a value set by the environment or policy can't be edited", { skip }, async () => {
  const programData = fs.mkdtempSync(path.join(os.tmpdir(), "fd-policy-"));
  fs.mkdirSync(path.join(programData, "FabricDesktop"));
  fs.writeFileSync(path.join(programData, "FabricDesktop", "policy.json"), JSON.stringify({ sql_page_rows: 2000 }));
  Object.assign(process.env, { ProgramData: programData, FABRIC_DESKTOP_CLIENT_ID: APP });
  try {
    app = await openApp({ engine: ["app_settings", "save_app_settings"] });
  } finally {
    delete process.env.FABRIC_DESKTOP_CLIENT_ID;
    delete process.env.ProgramData;
  }
  const { page } = app;
  await page.evaluate(() => { localStorage.removeItem("fd.fold.settings.advanced"); go("settings"); });
  await page.waitForSelector("section.advanced.folded");
  const headings = await page.locator(".page.settings > section h2").allTextContents();
  assert.ok(headings.includes("Account") && headings.includes("Advanced"), headings.join(", "));
  assert.ok(!(await page.locator("section.advanced .fold-body").isVisible()), "folded by default");
  assert.ok(!(await page.locator("section:not(.advanced)", { hasText: "App (client) ID" }).count()), "the App ID left Account");

  await page.click("button.fold-toggle:has-text('Advanced')");
  const id = page.getByRole("textbox", { name: "App (client) ID" });
  assert.strictEqual(await id.inputValue(), APP);
  assert.strictEqual(await id.getAttribute("readonly"), "");
  assert.strictEqual(await page.locator(".advanced-group:has(h3:text('Sign-in')) button:has-text('Save')").count(), 0);
  assert.match(await page.locator(".advanced-group:has(h3:text('Sign-in'))").textContent(), /Set by the FABRIC_DESKTOP_CLIENT_ID environment variable/);

  const rows = page.getByRole("spinbutton", { name: "Rows per page" });
  assert.strictEqual(await rows.inputValue(), "2000");
  assert.strictEqual(await rows.getAttribute("readonly"), "");
  const rowsSetting = page.locator(".setting", { has: rows });
  assert.match(await rowsSetting.textContent(), /Set by your organization's policy/);
  assert.strictEqual(await page.getByRole("spinbutton", { name: "Time limit" }).getAttribute("readonly"), null);

  // The engine refuses an ID the variable overrides instead of saying Saved.
  const refused = await page.evaluate(() => rpc("save_app_settings", { changes: { client_id: "" } }).then(() => "saved", (e) => String(e.message || e)));
  assert.match(refused, /FABRIC_DESKTOP_CLIENT_ID environment variable sets client_id/);

  await page.click("button.fold-toggle:has-text('Advanced')");
  assert.strictEqual(await page.evaluate(() => localStorage.getItem("fd.fold.settings.advanced")), "1");
  assert.deepStrictEqual(app.errors, []);
});

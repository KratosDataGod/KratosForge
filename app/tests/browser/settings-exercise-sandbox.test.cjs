// PB-131: Settings > Advanced > Exercise sandbox says whether Academy exercises run in an AppContainer
// and how many folders of KratosForge's Python it may read, and removes that permission after a
// confirm. A folder Windows wouldn't change is listed under the row with Try again and Copy the list.
// Runs the real engine for the settings; the sandbox calls are mocked, since they need Windows.
const { test, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings, settleForAxe } = require("./harness.cjs");
// axe covers dialogs (#1673, ui.test.cjs): the cases below run axe while these are open.
// axe covers dialogs: meta.js:confirmDialog

const skip = !playwright && "Playwright is not installed";
const noAxe = skip || (!axeSource && "axe-core is not installed");
const SHOTS = process.env.SCREENSHOT_DIR;
const FAILED = ["C:\\Users\\chris\\AppData\\Local\\FabricDesktop\\python\\Lib\\site-packages\\polars",
  "C:\\Users\\chris\\AppData\\Local\\FabricDesktop\\python\\Lib\\site-packages\\pyarrow"];

let app;
after(async () => { await app?.close(); });

async function open(status, removes) {
  await app?.close();
  const answers = [...removes];
  app = await openApp({ engine: ["app_settings", "save_app_settings"], mocks: {
    readiness_check: () => [],
    academy_sandbox_status: () => status(),
    academy_sandbox_remove_grants: () => answers.shift(),
  } });
  const { page } = app;
  await page.evaluate(() => { localStorage.setItem("fd.fold.settings.advanced", "0"); go("settings"); });
  await page.waitForSelector(".advanced-group:has(h3:text('Exercise sandbox')) .sandbox-status");
  const fold = page.locator("button.fold-toggle:has-text('Advanced')");
  if ((await fold.getAttribute("aria-expanded")) !== "true") await fold.click();
  return page;
}

// Screenshots after the fade-ins have ended (motion.js), as the person sees the page.
async function shot(page, target, name) {
  if (!SHOTS) return;
  await settleForAxe(page);
  await target.screenshot({ path: path.join(SHOTS, name) });
}

// The page can draw Settings again while it loads (a sign-in or settings read finishing), which
// redraws the row from "Looking...", so the checks wait for the text instead of reading it once.
const group = (page) => page.locator(".advanced-group:has(h3:text('Exercise sandbox'))");

test("the row says On with the folder count, and Remove asks first, then says how many", { skip }, async () => {
  let granted = 9;
  const page = await open(() => ({ available: true, why_not: "", enabled: true, granted }), [{ removed: 9, failed: [] }]);
  const row = group(page);
  await row.locator("text=On: exercises run in an AppContainer").waitFor();
  await row.getByText(/Read permission on 9 folders of KratosForge's Python/).first().waitFor();
  // It never claims more than the AppContainer gives, and says it isn't always used.
  await row.getByText(/When the AppContainer is on, exercise scripts \(except Delta and PySpark ones\)/).first().waitFor();
  await row.getByText(/no network and no access to your own files/).first().waitFor();
  await shot(page, row, "exercise-sandbox-row.png");

  await row.getByRole("button", { name: "Remove exercise permissions" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /Remove exercise permissions\?/);
  assert.match(await dialog.textContent(), /\(9 folders\)\. Your files and exercises aren't touched\./);
  assert.match(await dialog.textContent(), /To keep it off, don't run exercises on this PC\./);
  await shot(page, page, "exercise-sandbox-confirm.png");
  if (axeSource) assert.deepStrictEqual(await axeFindings(page, "[role=dialog]"), []);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  assert.ok(!app.calls.some((c) => c.method === "academy_sandbox_remove_grants"), "Cancel removes nothing");

  await row.getByRole("button", { name: "Remove exercise permissions" }).click();
  granted = 0;
  await page.getByRole("dialog").getByRole("button", { name: "Remove" }).click();
  await page.locator(".toast", { hasText: "Removed the exercise permissions from 9 folders" }).waitFor();
  await row.locator("text=No permissions held now. The next exercise adds them back.").waitFor();
  assert.strictEqual(await row.getByRole("button", { name: "Remove exercise permissions" }).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("folders Windows wouldn't change are listed, with Try again and Copy the list", { skip }, async () => {
  const page = await open(() => ({ available: true, why_not: "", enabled: true, granted: 9 }),
    [{ removed: 7, failed: FAILED }, { removed: 2, failed: [] }]);
  const row = group(page);
  await row.getByRole("button", { name: "Remove exercise permissions" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Remove" }).click();
  const warn = row.getByRole("alert");
  await warn.waitFor();
  assert.match(await warn.textContent(), /Removed from 7 folders\. Windows wouldn't change 2:/);
  assert.deepStrictEqual(await warn.locator("li").allTextContents(), FAILED);
  assert.match(await warn.textContent(), /A program may have them open\. Close other apps and try again\./);
  await warn.getByRole("button", { name: "Copy the list" }).waitFor();
  await shot(page, row, "exercise-sandbox-partial.png");
  if (axeSource) assert.deepStrictEqual(await axeFindings(page, ".advanced"), []);

  await warn.getByRole("button", { name: "Try again" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Remove" }).click();
  await page.locator(".toast", { hasText: "Removed the exercise permissions from 2 folders" }).waitFor();
  assert.strictEqual(await row.getByRole("alert").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Off says why, and the button shows only while permissions are held", { skip }, async () => {
  const page = await open(() => ({ available: false, why_not: "AppContainers are a Windows feature.", enabled: false, granted: 0 }), []);
  const row = group(page);
  await row.locator("text=Off: exercises run in a limited process on this PC").waitFor();
  await row.getByText(/Why: AppContainers are a Windows feature\./).first().waitFor();
  await row.getByText(/No permissions held\./).first().waitFor();
  assert.strictEqual(await row.getByRole("button", { name: "Remove exercise permissions" }).count(), 0);
  await shot(page, row, "exercise-sandbox-off.png");
});

test("a status the engine can't read shows the error with Retry", { skip }, async () => {
  let fail = true;
  const page = await open(() => { if (fail) throw new Error("The engine stopped"); return { available: true, why_not: "", enabled: true, granted: 3 }; }, []);
  const row = group(page);
  await row.locator(".error-panel", { hasText: "Couldn't read the exercise sandbox status" }).waitFor();
  await shot(page, row, "exercise-sandbox-error.png");
  fail = false;
  await row.getByRole("button", { name: "Retry" }).click();
  await row.locator("text=Read permission on 3 folders of KratosForge's Python").waitFor();
});

test("the Exercise sandbox row has no serious axe findings", { skip: noAxe }, async () => {
  const page = await open(() => ({ available: true, why_not: "", enabled: true, granted: 9 }), []);
  await group(page).getByRole("button", { name: "Remove exercise permissions" }).waitFor();
  assert.deepStrictEqual(await axeFindings(page, ".advanced"), []);
});

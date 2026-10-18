// #191: Home's Ready to work card remembers a passed write test, and a failed one forgets it
// (see harness.cjs for how to run it).
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
after(async () => { await app?.close(); });

const ok = (key, title) => ({ key, title, status: "ok", text: "Fine." });
const rows = [ok("engine", "Local engine"), ok("signin", "Signed in"), ok("fabric", "Fabric answers"),
  ok("capacity", "A workspace on a capacity"), ok("policy", "Your organization's settings")];

test("a failed write test clears the saved pass and the card asks again", { skip }, async () => {
  const saved = { user: "ana@contoso.com", tenant: "t1", workspace_id: "w1", lakehouse_id: "l1", workspace: "Sales Dev",
    at: new Date().toISOString() };
  app = await openApp({
    storage: { "fd.ready.write": JSON.stringify(saved) },
    mocks: {
      environment: () => ({ python: "Python 3 (test)", user: "ana@contoso.com", tenant: "t1" }),
      readiness_check: () => rows,
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev", capacityId: "c1" }],
      list_lakehouses: () => [{ id: "l1", displayName: "LH_Bronze" }],
      readiness_write_test: () => { throw new Error("PermissionError: You can't write to that lakehouse."); },
    },
  });
  const page = app.page;
  await page.evaluate(() => { state.user = "ana@contoso.com"; state.tenant = "t1"; document.body.append(readinessCard()); });
  await page.waitForSelector("text=Ready to work.");  // folded: the saved pass counts
  await page.click("button:has-text('Test again')");
  await page.waitForSelector("#dialog-root select:not([disabled])");
  await page.selectOption("#dialog-root select >> nth=0", "w1");
  await page.waitForSelector("#dialog-root select >> nth=1 >> option[value=l1]", { state: "attached" });
  await page.selectOption("#dialog-root select >> nth=1", "l1");
  await page.click("button:has-text('Write and delete the test file')");
  await page.waitForSelector("text=5 of 6 ready");
  assert.strictEqual(await page.evaluate(() => localStorage.getItem("fd.ready.write")), null);
  assert.ok(await page.locator("button:has-text('Test a write')").count() > 0);
  assert.deepStrictEqual(app.errors, []);
});

test("the card says how it works and names the one write", { skip }, async () => {
  const page = app.page;
  await page.evaluate(() => { localStorage.removeItem("fd.ready.write"); document.querySelectorAll(".ready").forEach((n) => n.remove());
    document.body.append(readinessCard()); });
  await page.waitForSelector(".ready .how-it-works");
  const text = await page.textContent(".ready .how-it-works");
  assert.match(text, /What it changes/);
  assert.match(text, /Files\/_fabric_desktop_check\/write-test\.txt/);
  assert.match(text, /asks you first/);
  assert.deepStrictEqual(app.errors, []);
});

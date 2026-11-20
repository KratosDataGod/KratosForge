// WR-250: promote into Prod waits for Prod's workspace name typed, as a Prod write does, and sends it as
// confirm_name for the engine to check again. Promote to Test asks nothing. The engine is mocked.
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const PROJECT = { environments: { dev: {},
  test: { workspace_id: "w1", workspace_name: "Sales Test", target: "onelake" },
  prod: { workspace_id: "w2", workspace_name: "Sales Prod", target: "onelake" } },
layers: [{ lakehouse: "LH_Gold" }] };

const apps = [];
async function open() {
  const sent = [];
  const app = await openApp({ mocks: {
    meta_promote_check: () => ({ errors: [], notes: [], version: "1.4.0" }),
    meta_promote_impact: () => ({}),
    meta_last_release: () => null,
    meta_promote: (p) => { sent.push(p); return { version: "1.4.0" }; },
  } });
  apps.push(app);
  await app.page.waitForFunction(() => typeof renderEnvTab === "function" && !!document.querySelector(".rail-item"));
  await app.page.evaluate(async (p) => {
    state.user = "chris";
    const body = document.createElement("div");
    body.id = "env-under-test";
    document.body.prepend(body);
    await renderEnvTab(body, p);
  }, PROJECT);
  return { ...app, sent };
}

async function until(check, ms = 15000) {
  const end = Date.now() + ms;
  while (!check()) {
    if (Date.now() > end) throw new Error("timed out");
    await new Promise((r) => setTimeout(r, 50));
  }
}

after(async () => { for (const app of apps) await app.close(); });

test("Promote to Prod waits for Prod's workspace name, then sends it", { skip }, async () => {
  const { page, sent, errors } = await open();
  await page.click("#env-under-test button:has-text('Promote to Prod')");
  const confirm = page.getByRole("dialog", { name: "Promote to Prod?" });
  await confirm.waitFor();
  assert.match(await confirm.innerText(), /This puts Test's metadata on Sales Prod, the Prod workspace/);
  const go = confirm.getByRole("button", { name: "Promote" });
  assert.ok(await go.isDisabled(), "Promote waits for the name");
  const box = confirm.getByRole("textbox", { name: "Type Sales Prod to confirm" });
  await box.fill("Sales Test");
  assert.ok(await go.isDisabled(), "another workspace's name doesn't count");
  await box.fill("Sales Prod");
  await go.click();
  await until(() => sent.length > 0);
  assert.deepStrictEqual(sent, [{ source: "test", target: "prod", confirm_name: "Sales Prod" }]);
  assert.deepStrictEqual(errors, []);
});

test("Cancel on the Prod confirm promotes nothing", { skip }, async () => {
  const { page, sent } = await open();
  await page.click("#env-under-test button:has-text('Promote to Prod')");
  const confirm = page.getByRole("dialog", { name: "Promote to Prod?" });
  await confirm.waitFor();
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await confirm.waitFor({ state: "detached" });
  await page.waitForTimeout(300);
  assert.deepStrictEqual(sent, []);
});

test("Promote to Test asks for no name", { skip }, async () => {
  const { page, sent } = await open();
  await page.click("#env-under-test button:has-text('Promote to Test')");
  await until(() => sent.length > 0);
  assert.deepStrictEqual(sent, [{ source: "dev", target: "test" }]);
});

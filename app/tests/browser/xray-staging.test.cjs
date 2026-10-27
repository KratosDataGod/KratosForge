// Leftover Dataflow Gen2 staging lakehouses in the Estate X-ray (docs/xray-fixes.md): Remove checks
// first and shows what the lakehouse holds; deleting needs the red confirm with "delete" typed; a
// lakehouse the check keeps has no way to delete it. Fabric is mocked.
const { test, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.XRAY_STAGING_SHOTS;  // a folder: save screenshots of each step there

const OLD = "StagingLakehouseForDataflows_20260926144305";
const MID = "StagingLakehouseForDataflows_20260926152759";
const NEW = "StagingLakehouseForDataflows_20260926153358";
const finding = (id, name) => ({ id, kind: "staging", severity: "low", action: "remove_staging", workspace_id: "ws",
  workspace_name: "Sales", lakehouse_id: id, lakehouse_name: name, kept: NEW, kept_id: "c",
  title: `${name} looks left over`,
  detail: `Dataflow Gen2 made it on 26 Sep 2026 to stage data, then made ${NEW}, which is newer and is kept. `
    + "Remove checks that nothing has been written to it for 30 days before it deletes anything." });
const RESULT = { scanned: "2026-10-26T10:00:00Z", workspaces: 1, lakehouses: 4, tables: 6, bytes: 2e8, skipped: [],
  findings: [finding("a", OLD), finding("b", MID)], lakehouse_rows: [] };
const PREVIEW = {
  a: { ok: true, why: [], lakehouse_name: OLD, created: "2026-09-26T14:43:05+00:00", kept: NEW, files: 214, bytes: 48.2 * 1024 ** 2,
       last_write: "2026-09-26T15:01:12+00:00", quiet_days: 30, allowed: true },
  b: { ok: false, lakehouse_name: MID, created: "2026-09-26T15:27:59+00:00", kept: NEW, files: 12, bytes: 3.1 * 1024 ** 2,
       last_write: new Date(Date.now() - 3 * 86400e3).toISOString(), quiet_days: 30, allowed: true,
       why: ["Something was written to it 3 days ago, less than 30 days, so a dataflow may still be using it."] },
};

const apps = [];
async function open(calls) {
  const app = await openApp({ mocks: {
    xray: () => ({ running: false, result: RESULT }),
    xray_staging_preview: (p) => { calls.push(["preview", p]); return PREVIEW[p.lakehouse_id]; },
    xray_remove_staging: (p) => { calls.push(["remove", p]); return { ...PREVIEW[p.lakehouse_id], removed_at: "2026-10-27T00:30:00+00:00" }; },
  } });
  apps.push(app);
  await app.page.setViewportSize({ width: 1180, height: 900 });
  await app.page.waitForFunction(() => typeof loadXray === "function" && !!document.querySelector(".rail-item"));
  await app.page.evaluate(async () => {
    await loadSettings();
    await new Promise((r) => setTimeout(r, 300));
    state.user = "chris";
    state.view = "home";
    const box = document.createElement("section");
    box.id = "xray-under-test";
    box.className = "card pad xray";
    document.querySelector("main, #main, body").prepend(box);
    xrayPage.kind = "staging";
    await loadXray(box, false);
  });
  return app;
}

const shot = async (page, name, el) => {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await (el ? page.locator(el).first() : page).screenshot({ path: path.join(SHOTS, `${name}.png`) });
};

after(async () => { for (const app of apps) await app.close(); });

test("Remove shows what it holds, then deletes only after the red confirm with delete typed", { skip }, async () => {
  const calls = [];
  const { page, errors } = await open(calls);
  const card = "#xray-under-test";
  await page.waitForSelector(`${card} .xray-item`);
  assert.match(await page.textContent(`${card} .xray-chips`), /Leftover staging2/);
  await shot(page, "1-finding", card);
  await page.locator(`${card} .xray-item`, { hasText: OLD }).getByRole("button", { name: "Remove" }).click();
  await page.waitForSelector(".dialog table");
  assert.match(await page.textContent(".dialog"), /214 files, 48\.2 MB/);
  assert.deepStrictEqual(calls.map((c) => c[0]), ["preview"], "it deleted before the confirm");
  await shot(page, "2-preview", ".dialog");
  await page.getByRole("button", { name: "Remove..." }).click();
  const ok = page.getByRole("button", { name: "Delete lakehouse" });
  assert.ok(await ok.isDisabled());
  await page.locator(".dialog input").fill("delete");
  await shot(page, "3-confirm", ".dialog");
  await ok.click();
  await page.waitForFunction(() => !document.querySelector(".dialog"));
  const removed = calls.find((c) => c[0] === "remove")[1];
  assert.deepStrictEqual(removed, { workspace_id: "ws", lakehouse_id: "a", lakehouse_name: OLD, workspace_name: "Sales", finding_id: "a" });
  assert.strictEqual(await page.locator(`${card} .xray-item`, { hasText: OLD }).count(), 0);
  assert.deepStrictEqual(errors, []);
});

test("A staging lakehouse the check keeps says why and can't be deleted", { skip }, async () => {
  const calls = [];
  const { page, errors } = await open(calls);
  await page.locator("#xray-under-test .xray-item", { hasText: MID }).getByRole("button", { name: "Remove" }).click();
  await page.waitForSelector(".dialog ul.reason");
  assert.match(await page.textContent(".dialog"), /stays\. Nothing will be deleted/);
  assert.ok(await page.getByRole("button", { name: "Remove..." }).isDisabled());
  await shot(page, "4-kept", ".dialog");
  assert.ok(!calls.some((c) => c[0] === "remove"));
  assert.deepStrictEqual(errors, []);
});

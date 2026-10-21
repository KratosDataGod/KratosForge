// UX-018: Remove on Monitor > Schedules asks first, in red, and Cancel removes nothing
// (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const removed = [];
let pc = [{ id: "s1", enabled: true, label: "nb_sales", text: "Daily at 06:00", next_run: "2026-10-21T06:00:00+00:00",
  target: { kind: "notebook", name: "nb_sales" }, last: null }];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    all_schedules: () => ({ pc, windows: true, fabric: { pending: false, items: [] }, runs: [], pc_set_aside: [] }),
    delete_pc_schedule: (p) => { removed.push(p.schedule_id); pc = []; return { deleted: p.schedule_id }; },
  } });
});
after(async () => { if (app) await app.close(); });

const clickRemove = async () => {
  await page().evaluate(() => { monitorPage.panel = "schedules"; go("monitor"); });
  await page().click("tr:has-text('nb_sales') button:has-text('Remove')");
  await page().waitForSelector(".dialog:has-text('Remove the schedule for nb_sales?')");
};

test("Remove asks first, says what stops, and Cancel keeps the schedule", { skip }, async () => {
  await clickRemove();
  const text = await page().textContent(".dialog");
  for (const part of ["Daily at 06:00.", "Windows task is deleted", "turn it Off"]) assert.ok(text.includes(part), part);
  assert.match(await page().getAttribute(".dialog button:has-text('Remove')", "class"), /danger-solid/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "monitor-schedules-remove-confirm.png") });
  await page().click(".dialog button:has-text('Cancel')");
  assert.deepStrictEqual(removed, []);
});

test("confirming removes it", { skip }, async () => {
  await clickRemove();
  await page().click(".dialog button:has-text('Remove')");
  await page().waitForFunction(() => !document.querySelector("tr td")?.textContent.includes("nb_sales"));
  assert.deepStrictEqual(removed, ["s1"]);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

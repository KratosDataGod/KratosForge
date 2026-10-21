// QA-011 follow-up: when this PC's schedules file couldn't be read and was moved aside,
// Monitor > Schedules says so and warns about Task Scheduler tasks (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const moved = [
  { file: "schedules.json.corrupt-20261020T090000000000Z", path: "C:\\Users\\chris\\AppData\\Local\\FabricDesktop\\schedules.json.corrupt-20261020T090000000000Z", at: "2026-10-20T09:00:00+00:00" },
  { file: "schedules.json.corrupt-20261018T090000000000Z", path: "C:\\x\\schedules.json.corrupt-20261018T090000000000Z", at: "2026-10-18T09:00:00+00:00" },
];
let setAside = moved;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    all_schedules: () => ({ pc: [], windows: true, fabric: { pending: false, items: [] }, runs: [], pc_set_aside: setAside }),
  } });
});
after(async () => { if (app) await app.close(); });

const open = async () => {
  await page().evaluate(() => { monitorPage.panel = "schedules"; go("monitor"); });
  await page().waitForSelector(".schedule-summary");
};

test("Monitor > Schedules says the schedules file was moved aside, and what that means", { skip }, async () => {
  await open();
  const text = await page().textContent(".set-aside");
  for (const part of ["Some schedules on this PC may be missing", "schedules.json.corrupt-20261020T090000000000Z",
    "Task Scheduler", "FabricDesktop", "1 older copy is kept next to it"]) assert.ok(text.includes(part), part);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "monitor-schedules-set-aside.png") });
});

test("nothing is said when no file was moved aside", { skip }, async () => {
  setAside = [];
  await page().evaluate(() => go("home"));
  await open();
  assert.strictEqual(await page().$(".set-aside"), null);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

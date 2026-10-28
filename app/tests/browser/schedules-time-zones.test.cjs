// PB-048: a Fabric schedule's time zone comes from every Windows time zone on this PC, the PC's own first,
// with a search box (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const calls = [];
const zones = [
  { id: "Hawaiian Standard Time", name: "(UTC-10:00) Hawaii" },
  { id: "Pacific Standard Time", name: "(UTC-08:00) Pacific Time (US & Canada)" },
  { id: "UTC", name: "(UTC) Coordinated Universal Time" },
  { id: "W. Australia Standard Time", name: "(UTC+08:00) Perth" },
  { id: "Cen. Australia Standard Time", name: "(UTC+09:30) Adelaide" },
  { id: "AUS Eastern Standard Time", name: "(UTC+10:00) Canberra, Melbourne, Sydney" },
  { id: "Tasmania Standard Time", name: "(UTC+10:00) Hobart" },
];
const fabRow = (zone) => ({ id: "f1", enabled: false, name: "pl_load_gold", type: "Pipeline", item_type: "DataPipeline", job_type: "Pipeline",
  workspace_id: "ws1", workspace_name: "Sales Prod", item_id: "pl1", text: `Daily at 02:00 ${zone}`, last: null,
  configuration: { type: "Daily", times: ["02:00"], localTimeZoneId: zone } });
let row = fabRow("Pacific Standard Time");

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    all_schedules: () => ({ pc: [], windows: true, fabric: { pending: false, items: [row], workspaces: 1 }, runs: [], pc_set_aside: [] }),
    time_zones: () => ({ zones, here: "AUS Eastern Standard Time" }),
    update_fabric_schedule: (p) => { calls.push(p); return { id: p.schedule_id }; },
  } });
});
after(async () => { if (app) await app.close(); });

const open = async () => {
  await page().evaluate(() => { monitorPage.panel = "fabric_schedules"; go("monitor"); });
  await page().click("tr:has-text('pl_load_gold') button:has-text('Edit')");
  await page().waitForSelector(".dialog:has-text('Edit the schedule for pl_load_gold')");
  await page().waitForFunction(() => document.querySelectorAll(".dialog select[aria-label='Time zone'] option").length >= 7);
};
const zoneSelect = () => page().locator(".dialog select[aria-label='Time zone']");
const visible = () => zoneSelect().evaluate((s) => [...s.options].filter((o) => !o.hidden).map((o) => o.value));

test("Every Windows time zone is offered, named the Windows way, with this PC's zone first", { skip }, async () => {
  await open();
  assert.strictEqual(await zoneSelect().inputValue(), "Pacific Standard Time", "the schedule's own zone stays chosen");
  const groups = await zoneSelect().evaluate((s) => [...s.querySelectorAll("optgroup")].map((g) => [g.label, g.children.length]));
  assert.deepStrictEqual(groups, [["On this PC", 1], ["All time zones", 6]]);
  assert.strictEqual(await zoneSelect().locator("option").first().textContent(), "(UTC+10:00) Canberra, Melbourne, Sydney · AUS Eastern Standard Time");
  await page().click(".dialog button:has-text('Cancel')");
});

test("Search narrows the list and picks the first match; Save sends the Windows id", { skip }, async () => {
  calls.length = 0;
  await open();
  const d = page().locator(".dialog");
  await d.locator("input[aria-label='Search time zones']").fill("aus");
  assert.deepStrictEqual(await visible(), ["AUS Eastern Standard Time", "W. Australia Standard Time", "Cen. Australia Standard Time"]);
  assert.strictEqual(await zoneSelect().inputValue(), "AUS Eastern Standard Time");
  await d.locator("input[aria-label='Search time zones']").fill("perth");
  assert.deepStrictEqual(await visible(), ["W. Australia Standard Time"]);
  assert.match(await d.textContent(), /After saving: .*W\. Australia Standard Time/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "schedule-time-zones.png") });
  await d.locator("button:has-text('Save changes')").click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.strictEqual(calls[0].schedule.time_zone, "W. Australia Standard Time");
  assert.strictEqual(calls[0].enabled, false, "a paused schedule stays paused");
});

test("A zone the PC doesn't list is kept", { skip }, async () => {
  calls.length = 0;
  row = fabRow("Mars Standard Time");
  await open();
  assert.strictEqual(await zoneSelect().inputValue(), "Mars Standard Time");
  await page().locator(".dialog button:has-text('Save changes')").click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.strictEqual(calls[0].schedule.time_zone, "Mars Standard Time");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

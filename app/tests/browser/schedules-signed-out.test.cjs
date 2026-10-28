// PB-048: this PC's schedules can also run while you're signed out of Windows; Windows asks for the
// password, and rows say when it needs it again (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const calls = [];
const row = (id, label, extra = {}) => ({ id, enabled: true, label, text: "Every day at 02:00", next_run: null,
  target: { kind: "notebook", name: label }, last: null, config: { type: "Daily", times: ["02:00"], localTimeZoneId: "Local" }, ...extra });
const rows = [
  row("a1", "nb_password", { signed_out: true, signed_out_problem: "password" }),
  row("b2", "nb_refused", { signed_out: true, signed_out_problem: "not_allowed" }),
  row("c3", "nb_plain"),
  row("d4", "nb_signed_out", { signed_out: true, signed_out_problem: null }),
];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    all_schedules: () => ({ pc: rows, windows: true, fabric: { pending: false, items: [], workspaces: 0 }, runs: [], pc_set_aside: [] }),
    pc_schedules_for: () => [rows[2]],
    set_pc_schedule_signed_out: (p) => { calls.push(["signed_out", p]); return {}; },
    update_pc_schedule: (p) => { calls.push(["update", p]); return {}; },
    add_pc_schedule: (p) => { calls.push(["add", p]); return { schedule: {}, windows: true }; },
  } });
});
after(async () => { if (app) await app.close(); });

const monitor = async () => {
  await page().evaluate(() => { monitorPage.panel = "schedules"; go("monitor"); });
  await page().waitForSelector("tr:has-text('nb_plain')");
};

test("Rows say they run signed out too, and what Windows needs when it can't", { skip }, async () => {
  calls.length = 0;
  await monitor();
  assert.match(await page().textContent("tr:has-text('nb_signed_out')"), /Also while signed out/);
  assert.doesNotMatch(await page().textContent("tr:has-text('nb_plain')"), /signed out/);
  assert.match(await page().textContent("tr:has-text('nb_password')"), /Windows needs your new password\. Enter it/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "schedules-signed-out.png") });
  await page().click("tr:has-text('nb_password') a:has-text('Enter it')");
  await page().click("tr:has-text('nb_refused') a:has-text('Run only while signed in')");
  for (let i = 0; i < 100 && calls.length < 2; i++) await page().waitForTimeout(20);
  assert.deepStrictEqual(calls, [["signed_out", { schedule_id: "a1", signed_out: true }], ["signed_out", { schedule_id: "b2", signed_out: false }]]);
});

test("Ticking it in Edit only asks Windows once and doesn't save the same times again", { skip }, async () => {
  calls.length = 0;
  await monitor();
  await page().click("tr:has-text('nb_plain') button:has-text('Edit')");
  const d = page().locator(".dialog");
  assert.ok(await d.locator(".signed-out-field .warn-box").isHidden(), "the cautions show once it is ticked");
  await d.locator("label:has-text('signed out of Windows') input").check();
  assert.ok(await d.locator(".signed-out-field .warn-box").isVisible());
  assert.match(await d.textContent(), /After saving: every day at 02:00, also while you're signed out/);
  await d.locator("button:has-text('Save changes')").click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.deepStrictEqual(calls, [["signed_out", { schedule_id: "c3", signed_out: true }]]);
});

test("New times on a signed-out schedule are one save; unticking turns it off first", { skip }, async () => {
  calls.length = 0;
  await monitor();
  await page().click("tr:has-text('nb_signed_out') button:has-text('Edit')");
  const d = page().locator(".dialog");
  assert.ok(await d.locator("label:has-text('signed out of Windows') input").isChecked());
  await d.locator("input[placeholder='06:00, 18:00']").fill("03:00");
  await d.locator("button:has-text('Save changes')").click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.deepStrictEqual(calls.map(([k]) => k), ["update"]);

  calls.length = 0;
  await page().click("tr:has-text('nb_signed_out') button:has-text('Edit')");
  await d.locator("label:has-text('signed out of Windows') input").uncheck();
  await d.locator("input[placeholder='06:00, 18:00']").fill("04:00");
  await d.locator("button:has-text('Save changes')").click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.deepStrictEqual(calls.map(([k, p]) => [k, p.signed_out]), [["signed_out", false], ["update", undefined]]);
});

test("The Schedule dialog adds a signed-out schedule on this PC, and hides the box for Fabric", { skip }, async () => {
  calls.length = 0;
  await page().evaluate(() => openScheduleDialog({ title: "Schedule nb_plain", what: "nb_plain",
    pc: { target: { kind: "notebook", name: "nb_plain" }, query: { kind: "notebook", name: "nb_plain" }, intro: "Runs it." },
    fabric: { intro: "In Fabric.", list: async () => ({ schedules: [] }), add: async () => ({}), remove: async () => ({}) } }));
  const d = page().locator(".dialog");
  await d.locator("label:has-text('signed out of Windows') input").check();
  await page().click(".dialog-actions button:has-text('Add schedule')");
  await page().waitForFunction(() => document.querySelector(".toast"));
  assert.strictEqual(calls[0][0], "add");
  assert.strictEqual(calls[0][1].signed_out, true);
  await d.locator(".seg button:has-text('Fabric')").click();
  assert.ok(await d.locator(".signed-out-field").isHidden());
  await page().click(".dialog button:has-text('Close')");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

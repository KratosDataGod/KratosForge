// PB-105 stage 4: Monitor > KratosForge > Operations > Incidents > Reports, the SLA report (see harness.cjs
// for how to run it). Reports sits beside Board on the view row, tagged Preview. It shows what
// incidents_measures returns for one environment and window: the summary (opened, resolved, still open,
// acknowledge and resolve SLA met), one row per priority, and the note on what's left out. A measure the
// engine has nothing for reads "Nothing to measure", an SLA with no target "No target", and one nobody has
// reached yet "None judged yet": never 0. Changing the window or environment reads again with those. No
// incidents gives one sentence and a next step; an engine error says so. When the incident tables need
// setting up, nothing is approved until the person confirms. Below 900 px the table becomes one card per
// priority. axe finds nothing serious. SCREENSHOT_DIR saves one picture of each mockup state.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, startupReads, axeSource, axeFindings, animationsDone } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const SHOTS = process.env.SCREENSHOT_DIR;

// As workloads/incident_history.report shapes them.
const spread = (count, median, p90) => ({ count, median_minutes: median, p90_minutes: p90 });
const NONE = { count: 0, median_minutes: null, p90_minutes: null };
const clock = (met, missed, breached) => {
  const judged = met + missed + breached;
  return { met, missed, breached, met_percent: judged ? Math.round((100 * met) / judged) : null };
};
const prio = (priority, opened, resolved, open, tta, ttr, ack, res) =>
  ({ priority, opened, resolved, open, time_to_acknowledge: tta, time_to_resolve: ttr, sla: { acknowledge: ack, resolve: res } });
function measured(env, days, priorities) {
  const sum = (k) => priorities.reduce((a, p) => a + p[k], 0);
  const both = (name) => clock(...["met", "missed", "breached"].map((k) => priorities.reduce((a, p) => a + p.sla[name][k], 0)));
  return { state: priorities.length ? "ready" : "empty", lakehouse: "FabricDesktop_Central", env, sla_source: "default", sla_problems: [],
    capped: false, copy: ["dev", "test", "prod"].map((e) => ({ env: e, state: "copied", events: 0, snapshots: 0 })),
    days, since: "2026-10-25T12:00:00Z",
    total: { opened: sum("opened"), resolved: sum("resolved"), open: sum("open"), time_to_acknowledge: NONE, time_to_resolve: NONE,
      sla: { acknowledge: both("acknowledge"), resolve: both("resolve") } },
    priorities };
}

// The mockup's states (/ux/sla-report/mockup.html), as the engine would return them.
const PROD_30 = measured("prod", 30, [
  prio("P1", 3, 3, 0, spread(3, 9, 14), spread(3, 190, 260), clock(3, 0, 0), clock(2, 1, 0)),
  prio("P2", 11, 10, 1, spread(11, 22, 48), spread(10, 410, 1380), clock(9, 1, 0), clock(8, 2, 0)),
  prio("P3", 19, 17, 2, spread(18, 95, 300), spread(17, 1720, 4300), clock(15, 2, 1), clock(13, 3, 1)),
  prio("P4", 9, 7, 2, spread(9, 240, 1100), spread(7, 2900, 8600), clock(7, 0, 0), clock(6, 0, 0)),
]);
// P4 has no target; one P2 has no acknowledge time; one P3 is still inside its resolve window.
const PARTIAL = measured("prod", 30, [
  prio("P2", 4, 4, 0, spread(3, 31, 55), spread(4, 520, 900), clock(2, 1, 0), clock(3, 0, 0)),
  prio("P3", 9, 6, 3, spread(9, 80, 260), spread(6, 1500, 3900), clock(7, 1, 0), clock(4, 1, 0)),
  prio("P4", 4, 2, 2, spread(4, 300, 900), spread(2, 3100, 3600), clock(0, 0, 0), clock(0, 0, 0)),
]);
const NOTHING = measured("test", 30, [prio("P3", 3, 0, 3, NONE, NONE, clock(0, 0, 0), clock(0, 0, 0))]);
const TARGETS = { P1: { acknowledge_minutes: 15, resolve_minutes: 240 }, P2: { acknowledge_minutes: 30, resolve_minutes: 240 },
  P3: { acknowledge_minutes: 240, resolve_minutes: 2880 }, P4: { acknowledge_minutes: null, resolve_minutes: null } };

let reports = {};  // "env:days" -> what incidents_measures returns; anything else has no incidents
let fail = null;  // the engine's error for the next reads
let tables = true;  // false: the incident tables need setting up, until a call approves it
const SETUP = "The central lakehouse needs the Incident management tables (fdinc_incident_event, fdinc_incident_snapshot). Confirm to continue.";

const mocks = {
  readiness_check: () => [],
  incidents_summary: () => ({ envs: ["dev", "test", "prod"].map((env) => ({ env, count: 0, incidents: [],
    observation: { state: "live", label: "Live", detail: "" } })), user: "chris@contoso.com" }),
  incidents_board: ({ env }) => ({ env, incidents: [], user: "chris@contoso.com", observation: { state: "live", label: "Live", detail: "" },
    agents: { readable: true, agents: [] }, counts: { open: 0, mine: 0, unassigned: 0, at_risk: 0, breached: 0 } }),
  incident_sla_policy: () => ({ targets: TARGETS, source: "default", problems: [], priorities: [] }),
  incidents_measures: ({ env, days, approve }) => {
    if (fail) throw new Error(fail);
    if (!tables && !approve) return { state: "needs_confirm", message: SETUP };
    tables = true;
    return reports[`${env}:${days}`] || measured(env, days, []);
  },
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await startupReads(app);
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  reports = { "prod:30": PROD_30 };
  fail = null;
  tables = true;
  await page().setViewportSize({ width: 1440, height: SHOTS ? 1180 : 940 });
  await page().evaluate(() => { closeDialog(); go("home"); });
});

const measures = () => app.calls.filter((c) => c.method === "incidents_measures").map((c) => c.params);

// Monitor > Operations > Incidents on its Reports view, for that environment and window; waits until
// the report (or its message) for them is drawn.
async function openReport(env = "prod", days = 30) {
  const from = measures().length;
  await page().evaluate(([env, days]) => {
    incPage.view = "reports"; incReport.env = env; incReport.days = days; monitorPage.panel = "incidents"; go("monitor");
  }, [env, days]);
  await drawn(from);
}

// Waits for the read after call number `from` to be drawn.
async function drawn(from) {
  for (const end = Date.now() + 5000; measures().length <= from; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) assert.fail("the report was never read");
  }
  await page().waitForFunction(() => {
    const box = document.querySelector(".page.monitor .inc-report");
    return box && !box.textContent.includes("Reading the report...");
  });
}

// One picture of a mockup state, to compare with /ux/sla-report/*.png, when SCREENSHOT_DIR is set.
async function shot(name) {
  if (!SHOTS) return;
  await animationsDone(page());
  await page().screenshot({ path: path.join(SHOTS, name) });
}

const text = (sel) => page().locator(`.page.monitor .inc-report ${sel}`).allInnerTexts();
const tiles = async () => (await text(".inc-report-tile")).map((t) => t.replace(/\s+/g, " ").trim());
async function rows() {
  return page().evaluate(() => [...document.querySelectorAll(".page.monitor .inc-report-table tbody tr")].map((tr) =>
    [...tr.children].map((td) => ({ text: td.innerText.replace(/\s+/g, " ").trim(), span: td.colSpan, cls: td.className }))));
}

test("Reports sits beside Board, tagged Preview, and opens on the SLA report for Prod, last 30 days", { skip }, async () => {
  await page().evaluate(() => { incPage.view = "board"; incPage.env = "test"; incReport.env = null; monitorPage.panel = "incidents"; go("monitor"); });
  await page().waitForSelector(".page.monitor .inc-views");
  const views = page().getByRole("group", { name: "Incidents view" });
  const labels = await views.getByRole("button").allInnerTexts();
  assert.strictEqual(labels.length, 2);
  assert.strictEqual(labels[0], "Board");
  assert.match(labels[1], /^Reports\s*Preview$/);
  assert.strictEqual(await views.getByRole("button", { name: "Board" }).getAttribute("aria-pressed"), "true");
  assert.strictEqual(await views.locator(".preview-tag").count(), 1, "Reports carries the app's Preview tag");
  const from = measures().length;
  await views.getByRole("button", { name: /^Reports/ }).click();
  await drawn(from);
  assert.strictEqual(await views.getByRole("button", { name: /^Reports/ }).getAttribute("aria-pressed"), "true");
  assert.deepStrictEqual(measures().at(-1), { env: "test", days: 30, approve: false }, "the board's environment, never approved by itself");
  await openReport("prod", 30);
  assert.deepStrictEqual(measures().at(-1), { env: "prod", days: 30, approve: false });
  const head = (await text(".inc-report-head"))[0];
  assert.match(head, /SLA report/);
  assert.strictEqual(await page().getByLabel("Environment").last().inputValue(), "prod");
  assert.strictEqual(await page().getByRole("group", { name: "Window" }).getByRole("button", { name: "30 days" }).getAttribute("aria-pressed"), "true");
  assert.deepStrictEqual(await page().getByRole("group", { name: "Window" }).getByRole("button").allInnerTexts(), ["7 days", "30 days", "90 days"]);
  await shot("1-prod-30-days.png");
});

test("the summary shows the engine's counts and SLA shares, with breaches named", { skip }, async () => {
  await openReport();
  assert.deepStrictEqual(await tiles(), [
    "Opened 42 in the last 30 days", "Resolved 37", "Still open 5",
    "Acknowledge SLA met 89% 34 of 38 on time, 1 breached", "Resolve SLA met 81% 29 of 36 on time, 1 breached"]);
  assert.strictEqual(await page().locator(".inc-report-tile .inc-report-breached").count(), 2);
  const note = (await text(".inc-report-note"))[0];
  assert.match(note, /^Nothing here is estimated\. An incident without the times a measure needs is left out of that measure, so counts can differ between columns\.\n/);
  assert.doesNotMatch(note, /For example/, "no incident is left out here");
  assert.match(note, /SLA counts use the targets set now\. An incident still inside its window, or a priority with no target, counts as neither met nor missed\. Missed means it was done late\. Breached means it's still open and already late\./);
});

test("the table has one row per priority, with times, and missed and breached marked", { skip }, async () => {
  await openReport();
  const heads = await page().evaluate(() => [...document.querySelectorAll(".inc-report-table thead tr")].map((tr) => [...tr.children].map((th) => th.innerText.trim())));
  assert.deepStrictEqual(heads, [["", "Incidents", "Time to acknowledge", "Time to resolve", "Acknowledge SLA", "Resolve SLA"],
    ["Priority", "Opened", "Resolved", "Open", "Median", "90th %", "Median", "90th %", "Met", "Missed", "Breached", "Met", "Missed", "Breached"]]);
  const r = await rows();
  assert.deepStrictEqual(r.map((cells) => cells.map((c) => c.text)), [
    ["P1 Many people blocked, no workaround", "3", "3", "0", "9 min", "14 min", "3 h 10 min", "4 h 20 min", "3", "0", "0", "2", "1", "0"],
    ["P2 A team blocked, or due soon", "11", "10", "1", "22 min", "48 min", "6 h 50 min", "23 h", "9", "1", "0", "8", "2", "0"],
    ["P3 Wrong or slow, has a workaround", "19", "17", "2", "1 h 35 min", "5 h", "1 d 4 h", "2 d 23 h", "15", "2", "1", "13", "3", "1"],
    ["P4 Minor, or a question", "9", "7", "2", "4 h", "18 h 20 min", "2 d", "5 d 23 h", "7", "0", "0", "6", "0", "0"]]);
  assert.match(r[0][12].cls, /inc-report-missed/);
  assert.match(r[2][10].cls, /inc-report-breached/);
  assert.doesNotMatch(r[0][9].cls, /inc-report-missed/, "a 0 isn't marked");
});

test("changing the window or the environment reads that report", { skip }, async () => {
  reports["prod:90"] = measured("prod", 90, [prio("P2", 2, 2, 0, spread(2, 10, 20), spread(2, 100, 200), clock(2, 0, 0), clock(1, 1, 0))]);
  reports["test:90"] = { ...NOTHING, days: 90 };
  await openReport();
  let from = measures().length;
  await page().getByRole("group", { name: "Window" }).getByRole("button", { name: "90 days" }).click();
  await drawn(from);
  assert.deepStrictEqual(measures().at(-1), { env: "prod", days: 90, approve: false });
  assert.strictEqual(await page().getByRole("group", { name: "Window" }).getByRole("button", { name: "90 days" }).getAttribute("aria-pressed"), "true");
  assert.strictEqual((await tiles())[0], "Opened 2 in the last 90 days");
  assert.deepStrictEqual((await rows()).map((cells) => cells[0].text), ["P2 A team blocked, or due soon"]);
  from = measures().length;
  await page().locator(".inc-report-head select").selectOption("test");
  await drawn(from);
  assert.deepStrictEqual(measures().at(-1), { env: "test", days: 90, approve: false });
  assert.strictEqual((await tiles())[0], "Opened 3 in the last 90 days");
  from = measures().length;
  await page().getByRole("group", { name: "Window" }).getByRole("button", { name: "7 days" }).click();
  await drawn(from);
  assert.deepStrictEqual(measures().at(-1), { env: "test", days: 7, approve: false });
  assert.match((await text(".inc-report-empty"))[0], /No incidents in Test in the last 7 days/);
});

test("a priority with no target, and an incident left out of a measure, read as the mockup shows", { skip }, async () => {
  reports["prod:30"] = PARTIAL;
  await openReport();
  assert.deepStrictEqual(await tiles(), ["Opened 17 in the last 30 days", "Resolved 12", "Still open 5",
    "Acknowledge SLA met 82% 9 of 11 on time", "Resolve SLA met 88% 7 of 8 on time"]);
  const r = await rows();
  const p4 = r.find((cells) => cells[0].text.startsWith("P4"));
  assert.deepStrictEqual(p4.map((c) => [c.text, c.span]), [["P4 Minor, or a question", 1], ["4", 1], ["2", 1], ["2", 1],
    ["5 h", 1], ["15 h", 1], ["2 d 3 h", 1], ["2 d 12 h", 1], ["No target", 3], ["No target", 3]]);
  const p3 = r.find((cells) => cells[0].text.startsWith("P3"));
  assert.deepStrictEqual(p3.slice(-3).map((c) => c.text), ["4", "1", "0"], "inside its window: neither met nor missed");
  assert.match((await text(".inc-report-note"))[0], /so counts can differ between columns\. For example, 1 P2 incident has no acknowledge time and isn't in that column\./);
  await shot("2-partial.png");
});

test("with nothing measurable, the tiles say None judged and the cells say why, never 0", { skip }, async () => {
  reports["test:30"] = NOTHING;
  await openReport("test", 30);
  assert.deepStrictEqual(await tiles(), ["Opened 3 in the last 30 days", "Resolved 0", "Still open 3",
    "Acknowledge SLA met None judged No incident has reached its target yet", "Resolve SLA met None judged No incident has reached its target yet"]);
  const [p3] = await rows();
  assert.deepStrictEqual(p3.map((c) => [c.text, c.span]), [["P3 Wrong or slow, has a workaround", 1], ["3", 1], ["0", 1], ["3", 1],
    ["Nothing to measure", 2], ["Nothing to measure", 2], ["None judged yet", 3], ["None judged yet", 3]]);
  const all = (await text("")).join("\n");
  assert.doesNotMatch(all, /\b0%|\b0 min|NaN|null|undefined/);
  assert.doesNotMatch((await text(".inc-report-note"))[0], /For example/, "open and not acknowledged isn't left out");
  await shot("3-nothing-measurable.png");
});

test("a window with no incidents gives one sentence and a next step", { skip }, async () => {
  await openReport("dev", 7);
  assert.deepStrictEqual(measures().at(-1), { env: "dev", days: 7, approve: false });
  assert.deepStrictEqual((await text(".inc-report-empty"))[0].split("\n").map((s) => s.trim()).filter(Boolean),
    ["No incidents in Dev in the last 7 days", "Pick a longer window or another environment to see a report."]);
  assert.strictEqual(await page().locator(".inc-report-tile, .inc-report-table, .inc-report-note").count(), 0);
  await shot("4-no-incidents.png");
});

test("an engine error says the report couldn't be read, and the pickers stay to try again", { skip }, async () => {
  fail = "TimeoutError: The central lakehouse didn't answer in time.";
  await openReport();
  const msg = (await text(".reason"))[0];
  assert.match(msg, /^Couldn't read the report: /);
  assert.strictEqual(await page().locator(".inc-report-tile").count(), 0, "no numbers from a failed read");
  fail = null;
  const from = measures().length;
  await page().getByRole("group", { name: "Window" }).getByRole("button", { name: "30 days" }).click();
  await drawn(from);
  assert.strictEqual((await tiles())[0], "Opened 42 in the last 30 days");
});

test("when the incident tables need setting up, it asks first and approves only on Create and read", { skip }, async () => {
  tables = false;
  await openReport();
  assert.deepStrictEqual(measures().at(-1), { env: "prod", days: 30, approve: false });
  const body = (await text(".monitor-empty"))[0];
  assert.match(body, /needs the Incident management tables/);
  assert.doesNotMatch(body, /Confirm to continue/);
  await page().getByRole("button", { name: "Set up history" }).click();
  await page().waitForSelector(".dialog");
  await page().locator(".dialog").getByRole("button", { name: "Cancel" }).click();
  assert.ok(measures().every((p) => p.approve === false), "nothing approved on Cancel");
  await page().getByRole("button", { name: "Set up history" }).click();
  await page().waitForSelector(".dialog");
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  const from = measures().length;
  await page().locator(".dialog").getByRole("button", { name: "Create and read" }).click();
  await drawn(from);
  assert.deepStrictEqual(measures().at(-1), { env: "prod", days: 30, approve: true });
  assert.strictEqual((await tiles())[0], "Opened 42 in the last 30 days");
});

test("below 900 px the table becomes one card per priority", { skip }, async () => {
  await page().setViewportSize({ width: 860, height: SHOTS ? 1400 : 1000 });
  await openReport();
  assert.ok(!(await page().locator(".inc-report-table").isVisible()), "the wide table is hidden");
  const cards = (await text(".inc-report-card")).map((t) => t.replace(/\s+/g, " ").trim());
  assert.strictEqual(cards.length, 4);
  assert.strictEqual(cards[0], "P1 Many people blocked, no workaround Incidents 3 opened, 3 resolved, 0 open "
    + "To acknowledge median 9 min, 90th % 14 min To resolve median 3 h 10 min, 90th % 4 h 20 min "
    + "Acknowledge SLA 3 met, 0 missed, 0 breached Resolve SLA 2 met, 1 missed, 0 breached");
  reports["prod:30"] = PARTIAL;
  await openReport();
  assert.match((await text(".inc-report-card"))[2].replace(/\s+/g, " "), /Acknowledge SLA No target Resolve SLA No target/);
  reports["prod:30"] = PROD_30;
  await openReport();
  await shot("5-narrow.png");
});

test("axe finds nothing serious on the report", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await openReport();
  assert.deepStrictEqual(await axeFindings(page(), ".page.monitor"), []);
});

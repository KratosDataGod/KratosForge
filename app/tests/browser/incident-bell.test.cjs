// Incidents stage 3: the bell's For you filter (see harness.cjs for how to run it). Incidents assigned
// to you in any environment (incidents_for_me) count on the bell while they wait on you, and list under
// For you: waiting on you first, with Acknowledge and Open. Acknowledge asks first and sends exactly that
// incident's environment and id; the count then drops. An environment that couldn't be read is named.
// With nothing assigned and nothing unreadable, the filter isn't shown. axe finds nothing serious.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const at = (s) => new Date(Date.now() + s * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");

// As incident_desk.incidents_for_me returns them.
const sla = (state, ack) => ({ priority: "P2", state, acknowledge: ack, resolve: { state: "due", due: at(14400), left_minutes: 240 } });
function fresh() {
  return {
    user: "chris@contoso.com",
    incidents: [
      { env: "prod", id: "inc-a", number: "INC-PROD-0042", title: "silver.orders failed", state: "open", kind: "run_failed", priority: "P1",
        sla: { ...sla("breached", { state: "breached", due: at(-720), over_minutes: 12 }), priority: "P1" }, at: at(-1500) },
      { env: "test", id: "inc-b", number: "INC-TEST-0007", title: "Revenue looks doubled", state: "open", kind: "reported", priority: "P3",
        sla: { ...sla("due", { state: "due", due: at(3600), left_minutes: 60 }), priority: "P3" }, at: at(-600) },
      { env: "dev", id: "inc-c", number: "INC-DEV-0003", title: "gold.revenue is late", state: "acknowledged", kind: "late", priority: "P4",
        sla: { priority: "P4", state: "none", acknowledge: { state: "none" }, resolve: { state: "none" } }, at: at(-300) },
    ],
    unreadable: [{ env: "dev", detail: "Your sign-in can't read this." }],
  };
}

let mine = fresh();
const acked = [];
before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      incidents_for_me: () => mine,
      incident_acknowledge: ({ env, id, note }) => {
        acked.push({ env, id, note });
        const i = mine.incidents.find((x) => x.env === env && x.id === id);
        i.state = "acknowledged";
        return { ...i, acknowledged_by: "chris@contoso.com" };
      },
    },
  });
  await startupReads(app);
  await page().waitForFunction(() => typeof loadIncidentsForMe === "function");
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  mine = fresh();
  acked.length = 0;
  await page().evaluate(async () => { closeDialog(); toggleTray(false); await loadIncidentsForMe(); });
});

const bell = () => page().evaluate(() => ({ label: document.getElementById("bell").getAttribute("aria-label"),
  badge: document.querySelector("#bell .badge")?.textContent || "" }));

test("the bell counts incidents waiting on you, and For you lists them waiting first", { skip }, async () => {
  const b = await bell();
  assert.match(b.label, /2 incidents waiting on you/);
  assert.ok(Number(b.badge) >= 2);
  await page().evaluate(() => toggleTray(true));
  await page().getByRole("button", { name: "For you 3" }).click();
  const items = await page().locator(".tray-item").allTextContents();
  assert.strictEqual(items.length, 3);
  assert.ok(items[0].includes("INC-PROD-0042 silver.orders failed") && items[0].includes("Waiting on you") && items[0].includes("Prod"));
  assert.ok(items[0].includes("Acknowledge overdue 12 min"));
  assert.ok(items[2].includes("Acknowledged, assigned to you") && !items[2].includes("Acknowledge ") );
  const tray = await page().textContent(".tray");
  assert.ok(tray.includes("Couldn't read Dev, so incidents there aren't listed."));
  assert.strictEqual(await page().locator(".tray-item").nth(2).getByRole("button", { name: "Acknowledge" }).count(), 0);
});

test("Acknowledge asks first, sends that incident only, and the count drops", { skip }, async () => {
  await page().evaluate(() => toggleTray(true));
  await page().getByRole("button", { name: "For you 3" }).click();
  await page().locator(".tray-item").nth(1).getByRole("button", { name: "Acknowledge" }).click();
  await page().waitForSelector(".dialog");
  assert.strictEqual(acked.length, 0, "nothing is sent before the dialog is confirmed");
  await page().locator(".dialog").getByLabel("Note (optional)").fill("Looking now");
  await page().locator(".dialog").getByRole("button", { name: "Acknowledge", exact: true }).click();
  for (const end = Date.now() + 5000; !acked.length; await new Promise((r) => setTimeout(r, 25))) if (Date.now() > end) break;
  assert.deepStrictEqual(acked, [{ env: "test", id: "inc-b", note: "Looking now" }]);
  await page().waitForFunction(() => /1 incident waiting on you/.test(document.getElementById("bell").getAttribute("aria-label")));
});

test("Open goes to that incident's page", { skip }, async () => {
  await page().evaluate(() => toggleTray(true));
  await page().getByRole("button", { name: "For you 3" }).click();
  await page().locator(".tray-item").first().getByRole("button", { name: "Open" }).click();
  assert.deepStrictEqual(await page().evaluate(() => [state.view, incPage.env_of, incPage.id, activity.open]), ["incident", "prod", "inc-a", false]);
});

test("with nothing assigned and nothing unreadable, there's no For you filter or count", { skip }, async () => {
  mine = { user: "chris@contoso.com", incidents: [], unreadable: [] };
  await page().evaluate(async () => { await loadIncidentsForMe(); toggleTray(true); });
  assert.strictEqual(await page().getByRole("button", { name: /^For you/ }).count(), 0);
  assert.doesNotMatch((await bell()).label, /waiting on you/);
});

test("axe finds nothing serious in the For you list", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await page().evaluate(() => toggleTray(true));
  await page().getByRole("button", { name: "For you 3" }).click();
  assert.deepStrictEqual(await axeFindings(page(), ".tray"), []);
});

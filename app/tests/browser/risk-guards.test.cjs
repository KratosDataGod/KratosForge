// Run risk: when the risk check itself can't run, the dialog says why and leaves the choice to the
// person. Cancel stops the run and Continue starts it, and neither records an acknowledgement, since
// nothing was checked. Past warnings that need a tick, Run anyway without it refuses and keeps the
// dialog; with it, the acknowledgement is recorded and the run goes ahead. Mocks only.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const acks = [];
let checks = 0;
let warn = false;
const WARNING = { id: "r-warning", result: "warning", label: "Warning", summary: "1 thing to check before you run",
  findings: [{ check: "history", outcome: "warning", label: "Warning", title: "The last run failed", detail: "A run now may fail the same way.",
    evidence: [], remedy: "", source: "", observed_at: null, essential: false, measurement: null, can_override: true }],
  can_proceed: true, needs_acknowledgement: true, note: "Read only: nothing was started, changed or paused to work this out.", reads: [] };

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      risk_check: () => { checks += 1; if (warn) return WARNING; throw new Error("This notebook isn't deployed to a Fabric workspace yet."); },
      risk_acknowledge: (p) => { acks.push(p); return { at: "now" }; },
    },
  });
  await page().waitForFunction(() => typeof riskGate === "function");
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

const TARGET = { id: "nb:daily_sales", action: "run_fabric", env: "dev", title: "Run daily_sales in Fabric?" };
const gate = () => page().evaluate((t) => { window.__gate = riskGate(t); }, TARGET);
const answer = () => page().evaluate(() => window.__gate);

test("a check that couldn't run says why and offers Cancel and Continue only", { skip }, async () => {
  checks = 0;
  await gate();
  await page().waitForSelector(".dialog");
  const text = await page().textContent(".dialog");
  assert.ok(text.includes("Run daily_sales in Fabric?") && text.includes("isn't deployed to a Fabric workspace yet"), text);
  assert.ok(text.includes("Continue runs it anyway"), text);
  const labels = await page().locator(".dialog .dialog-actions button, .dialog footer button").allTextContents();
  assert.ok(labels.some((l) => l.includes("Cancel")) && labels.some((l) => l.includes("Continue")), labels.join("|"));
  assert.ok(!labels.some((l) => l.includes("Run anyway")), labels.join("|"));
  assert.strictEqual(checks, 1);
  await page().locator(".dialog").getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(await answer(), false);
});

test("Cancel stops the run and records nothing", { skip }, async () => {
  acks.length = 0;
  await gate();
  await page().locator(".dialog").getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(await answer(), false);
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.deepStrictEqual(acks, []);
});

test("Continue starts the run without an acknowledgement", { skip }, async () => {
  acks.length = 0;
  await gate();
  await page().locator(".dialog").getByRole("button", { name: "Continue" }).click();
  assert.strictEqual(await answer(), true);
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.deepStrictEqual(acks, []);
});

test("Run anyway past warnings needs the tick: without it nothing is recorded and the dialog stays", { skip }, async () => {
  warn = true;
  acks.length = 0;
  await gate();
  await page().locator(".dialog").getByRole("button", { name: "Run anyway" }).click();
  await page().waitForFunction(() => document.getElementById("toast")?.textContent.includes("Tick"));
  assert.ok(await page().locator(".dialog").isVisible());
  assert.deepStrictEqual(acks, []);
  await page().locator(".dialog").getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(await answer(), false);
  warn = false;
});

test("Run anyway with the tick records the acknowledgement and goes ahead", { skip }, async () => {
  warn = true;
  acks.length = 0;
  await gate();
  await page().locator(".dialog .risk-ack input").check();
  await page().locator(".dialog").getByRole("button", { name: "Run anyway" }).click();
  assert.strictEqual(await answer(), true);
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.deepStrictEqual(acks, [{ id: "nb:daily_sales", action: "run_fabric", env: "dev", risk_id: "r-warning" }]);
  warn = false;
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));

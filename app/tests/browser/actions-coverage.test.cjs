// The action registry (actions.js), the paths the other tests leave out: an action without the views it
// belongs to or with an unknown place, a recommendation with an unknown reason, and an action whose
// availability check throws, which is shown as "couldn't be checked" when someone runs it.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {} });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });
beforeEach(() => { if (!skip) app.errors.length = 0; });

test("an action needs its views and a known place; a recommendation needs a known reason", { skip }, async () => {
  const out = await page().evaluate(() => {
    const problem = (fn) => { try { fn(); return null; } catch (e) { return e.message; } };
    const base = { id: "t.cov", label: "Cov", risk: "low", where: "local", contexts: ["home"], run: () => 1 };
    return [
      problem(() => defineAction({ ...base, contexts: [] })),
      problem(() => defineAction({ ...base, where: "cloud" })),
      problem(() => defineRecommendation({ id: "t.rec", contexts: ["home"], reason: "whim", basis: "verified", action: "x",
        when: () => null, why: () => "" })),
      actionRegistry.actions.has("t.cov"),
      actionRegistry.recommendations.some((r) => r.id === "t.rec"),
    ];
  });
  assert.deepStrictEqual(out, [
    "Action t.cov: needs the views it belongs to (contexts)",
    "Action t.cov: where must be one of local, fabric, either",
    "Recommendation t.rec: reason must be one of failure, safety, validation, unfinished, local, continue",
    false, false]);
  assert.deepStrictEqual(app.errors, []);
});

test("an action whose check throws says it couldn't be checked, and doesn't run", { skip }, async () => {
  const out = await page().evaluate(async () => {
    let ran = 0;
    defineAction({ id: "t.broken", label: "Broken check", risk: "low", where: "local", contexts: [state.view],
      available: () => { throw new Error("no"); }, run: () => { ran++; return 1; } });
    const s = actionState("t.broken", { view: state.view });
    const result = await runAction("t.broken", { view: state.view });
    return { available: s.available, reason: s.reason, result: result === undefined, ran };
  });
  assert.deepStrictEqual(out, { available: false, reason: "It couldn't be checked just now.", result: true, ran: 0 });
  await page().waitForFunction(() => {
    const el = document.getElementById("toast");
    return !el.hidden && el.textContent.startsWith("Broken check: It couldn't be checked just now.");
  }, null, { timeout: 10000 });
  await page().evaluate(() => {
    actionRegistry.actions.delete("t.broken");
    const i = features.commands.findIndex((c) => c.action === "t.broken");
    if (i >= 0) features.commands.splice(i, 1);
  });
  assert.deepStrictEqual(app.errors, []);
});

// The action registry (actions.js) beyond actions-coverage and the other action tests: every reason an action
// or a recommendation is refused at definition, a context provider that throws, an action that isn't there or
// isn't available, a confirm given as a function that is cancelled, a risk check that stops the run, the action
// bar with nothing primary and recommendations of equal standing ("You could"), a skin's command group with a
// split action, and its redraw keeping the focus on the split's chevron or the first button. Actions are defined
// for a view of the test's own; nothing reaches the engine.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {} });
  await page().evaluate(() => {
    window.__ran = [];
    const ctx = ["t3view"];
    defineAction({ id: "t3.a", label: "Alpha", risk: "low", where: "local", contexts: ctx, run: () => { window.__ran.push("a"); return 1; } });
    defineAction({ id: "t3.b", label: "Beta", risk: "low", where: "fabric", contexts: ctx, available: () => false, run: () => 2 });
    defineAction({ id: "t3.c", label: "Gamma", risk: "high", where: "either", contexts: ctx,
      confirm: (c) => ({ title: `Gamma in ${c.view}?`, text: "It can't be undone." }), run: () => { window.__ran.push("c"); return 3; } });
    defineAction({ id: "t3.d", label: "Delta", risk: "low", where: "fabric", contexts: ctx, spendsCapacity: true,
      riskCheck: () => ({ id: "rc", result: "blocked" }), run: () => { window.__ran.push("d"); return 4; } });
    defineAction({ id: "t3.e", label: "Epsilon", risk: "low", where: "local", contexts: ctx, split: () => [btn("Epsilon option", null, () => {}, "tool")],
      run: () => 5 });
    defineAction({ id: "t3.f", label: "Zeta", risk: "low", where: "local", contexts: ["t3bare"], run: () => 6 });
    defineRecommendation({ id: "t3.r1", contexts: ctx, reason: "unfinished", basis: "estimate", action: "t3.a", when: () => ({ n: 1 }), why: () => "One thing left",
      label: (e) => `Finish ${e.n}` });
    defineRecommendation({ id: "t3.r2", contexts: ctx, reason: "unfinished", basis: "verified", action: "t3.e", when: () => ({}), why: () => "Another" });
    defineRecommendation({ id: "t3.r3", contexts: ctx, reason: "failure", basis: "verified", action: "t3.b", when: () => ({}), why: () => "Not available, so not shown" });
  });
});
after(async () => { if (app) await app.close(); });
beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => { window.__ran = []; closeDialog(); closeMenu(); toastClose(); });
  app.errors.length = 0;
});

test("an action or a recommendation is refused for each thing it lacks", { skip }, async () => {
  const out = await page().evaluate(() => {
    const problem = (fn) => { try { fn(); return null; } catch (e) { return e.message; } };
    const a = { id: "t3.x", label: "X", risk: "low", where: "local", contexts: ["t3view"], run: () => 1 };
    const r = { id: "t3.rx", contexts: ["t3view"], reason: "failure", basis: "verified", action: "t3.a", when: () => null, why: () => "" };
    return [
      problem(() => defineAction(null)), problem(() => defineAction({ ...a, id: "t3.a" })), problem(() => defineAction({ ...a, label: "" })),
      problem(() => defineAction({ ...a, run: null })),
      problem(() => defineRecommendation(null)), problem(() => defineRecommendation({ ...r, id: "t3.r1" })),
      problem(() => defineRecommendation({ ...r, contexts: [] })), problem(() => defineRecommendation({ ...r, basis: "hunch" })),
      problem(() => defineRecommendation({ ...r, action: "" })), problem(() => defineRecommendation({ ...r, why: null })),
    ];
  });
  assert.deepStrictEqual(out, [
    "Action (no id): needs an id", "Action t3.a: is defined twice", "Action t3.x: needs a label", "Action t3.x: needs run(ctx)",
    "Recommendation (no id): needs an id", "Recommendation t3.r1: is defined twice",
    "Recommendation t3.rx: needs the views it belongs to (contexts)", "Recommendation t3.rx: basis must be verified or estimate",
    "Recommendation t3.rx: needs the action it leads to", "Recommendation t3.rx: needs when(ctx) and why(evidence)",
  ]);
  assert.deepStrictEqual(app.errors, []);
});

test("a provider that throws leaves the bare view; an unknown or unavailable action doesn't run", { skip }, async () => {
  const out = await page().evaluate(async () => {
    const view = state.view;
    provideActionContext("t3view", () => { throw new Error("broken"); });
    state.view = "t3view";
    const ctx = actionContext();
    state.view = view;
    return { ctx, unknown: actionState("t3.none", ctx), runUnknown: await runAction("t3.none", ctx), runB: await runAction("t3.b", ctx),
      toast: document.getElementById("toast").textContent };
  });
  assert.deepStrictEqual(out, { ctx: { view: "t3view" }, unknown: null, runUnknown: undefined, runB: undefined, toast: "Beta: It isn't available here." });
  assert.deepStrictEqual(app.errors, []);
});

test("a confirm from a function that is cancelled, and a risk check that stops, run nothing", { skip }, async () => {
  const ran = page().evaluate(() => runAction("t3.c", { view: "t3view" }));
  const d = page().getByRole("dialog", { name: "Gamma in t3view?" });
  await d.waitFor();
  await d.getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(await ran, undefined);
  const out = await page().evaluate(async () => {
    const gate = riskGate;
    riskGate = async () => false;
    try { return await runAction("t3.d", { view: "t3view" }); } finally { riskGate = gate; }
  });
  assert.strictEqual(out, undefined);
  assert.deepStrictEqual(await page().evaluate(() => window.__ran), []);
  assert.deepStrictEqual(app.errors, []);
});

test("with nothing primary and no ranked recommendation the bar has only More; equal reasons read You could", { skip }, async () => {
  const out = await page().evaluate(() => {
    const bar = actionBar({ view: "t3bare" });
    const steps = nextSteps({ view: "t3view" });
    return { bar: [...bar.querySelectorAll(":scope > button")].map((b) => b.textContent.trim()), head: steps.getAttribute("aria-label"),
      labels: [...steps.querySelectorAll(".next-step button")].map((b) => b.textContent.trim()),
      pills: [...steps.querySelectorAll(".pill-s")].map((p) => p.className), none: nextSteps({ view: "nowhere" }) };
  });
  assert.deepStrictEqual(out.bar, ["More"]);
  assert.strictEqual(out.head, "You could");
  assert.deepStrictEqual(out.labels, ["Finish 1", "Epsilon"]);
  assert.deepStrictEqual(out.pills, ["pill-s neu", "pill-s info"]);
  assert.strictEqual(out.none, null);
  assert.deepStrictEqual(app.errors, []);
});

test("a skin's group shows a split action's chevron, and its redraw keeps the focus there or on the first button", { skip }, async () => {
  const out = await page().evaluate(() => {
    const placed = skinCommands;
    const ctxOf = actionContext;
    skinCommands = (view) => (view === "t3view" ? { primary: "t3.e", bar: ["t3.a"] } : null);
    actionContext = () => ({ view: "t3view" });
    try {
      const host = document.createElement("div");
      document.body.append(host);
      host.append(skinCommandGroup());
      const chevron = host.querySelector('[aria-label="Epsilon options"]');
      chevron.focus();
      refreshSkinCommandGroup();
      const kept = document.activeElement?.getAttribute("aria-label");
      // A focused element the redraw can't find again: the first button takes the focus.
      const odd = document.createElement("span");
      odd.tabIndex = 0;
      host.querySelector(".skin-commands").append(odd);
      odd.focus();
      refreshSkinCommandGroup();
      const first = document.activeElement?.textContent.trim();
      host.remove();
      return { kept, first, buttons: chevron ? true : false };
    } finally { skinCommands = placed; actionContext = ctxOf; }
  });
  assert.deepStrictEqual(out, { kept: "Epsilon options", first: "Epsilon", buttons: true });
  assert.deepStrictEqual(app.errors, []);
});

// The action-first framework (actions.js): actions say why they can't run, high-risk ones ask with a
// danger dialog, recommendations rank by reason alone and change with the state they read, and every
// action is in the command bar (see harness.cjs for how to run it).
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
after(async () => { await app?.close(); });

async function page() {
  if (!app) {
    app = await openApp({ mocks: { list_notebooks: () => [], readiness_check: () => [] } });
    await app.page.waitForFunction(() => state.view === "home" && !state.starting);
    // A small screen of its own, so nothing the app defines changes what these tests see.
    await app.page.evaluate(() => {
      window.t = { warnings: 0, ran: [], unsaved: false, signedIn: true };
      provideActionContext("home", () => ({ warnings: t.warnings }));
      defineAction({ id: "t.run", label: "Run the test thing", contexts: ["home"], risk: "moderate", where: "local", primary: true,
        run: () => { t.ran.push("run"); return { rows: 3 }; }, done: (r) => ({ text: `Wrote ${r.rows} rows`, next: ["t.open"] }) });
      defineAction({ id: "t.open", label: "Open the test table", contexts: ["home"], risk: "low", where: "local",
        run: () => { t.ran.push("open"); return true; } });
      defineAction({ id: "t.validate", label: "Review the test warnings", contexts: ["home"], risk: "low", where: "local",
        run: () => { t.ran.push("validate"); return true; } });
      defineAction({ id: "t.save", label: "Save the test thing", contexts: ["home"], risk: "low", where: "local",
        run: () => { t.ran.push("save"); return true; } });
      defineAction({ id: "t.publish", label: "Publish the test thing", contexts: ["home"], risk: "moderate", where: "fabric",
        available: () => (t.signedIn ? true : "Sign in to publish to Fabric."), run: () => { t.ran.push("publish"); return true; } });
      defineAction({ id: "t.drop", label: "Delete the test table", contexts: ["home"], risk: "high", where: "fabric",
        confirm: { title: "Delete the test table?", text: "Its rows are gone for good.", verb: "Delete" },
        run: () => { t.ran.push("drop"); return true; } });
      defineAction({ id: "t.elsewhere", label: "Something on another page", contexts: ["monitor"], risk: "low", where: "local", run: () => true });
      defineRecommendation({ id: "t.rec.warnings", contexts: ["home"], reason: "validation", basis: "verified", action: "t.validate",
        when: (ctx) => (ctx.warnings ? { n: ctx.warnings } : null), why: (e) => `${e.n} validation warnings` });
      defineRecommendation({ id: "t.rec.save", contexts: ["home"], reason: "unfinished", basis: "verified", action: "t.save",
        when: () => (t.unsaved ? true : null), why: () => "Unsaved changes" });
      defineRecommendation({ id: "t.rec.publish", contexts: ["home"], reason: "unfinished", basis: "verified", action: "t.publish",
        when: () => (t.unsaved ? true : null), why: () => "Not published yet" });
      defineRecommendation({ id: "t.rec.broken", contexts: ["home"], reason: "failure", basis: "verified", action: "t.open",
        when: () => { throw new Error("can't read"); }, why: () => "never shown" });
    });
  }
  return app.page;
}

test("a high-risk action can't be defined without asking first, nor capacity spent without the risk check", { skip }, async () => {
  const p = await page();
  const problems = await p.evaluate(() => {
    const tryDefine = (a) => { try { defineAction(a); return null; } catch (err) { return err.message; } };
    return [
      tryDefine({ id: "t.bad1", label: "Drop", contexts: ["home"], risk: "high", where: "fabric", run: () => true }),
      tryDefine({ id: "t.bad2", label: "Spend", contexts: ["home"], risk: "moderate", where: "fabric", spendsCapacity: true, run: () => true }),
      tryDefine({ id: "t.bad3", label: "Odd", contexts: ["home"], risk: "medium", where: "local", run: () => true }),
      tryDefine({ id: "t.run", label: "Again", contexts: ["home"], risk: "low", where: "local", run: () => true }),
    ];
  });
  assert.match(problems[0], /high risk, so it needs confirm or ownConfirm/);
  assert.match(problems[1], /spends capacity, so it needs riskCheck or ownRiskCheck/);
  assert.match(problems[2], /risk must be one of low, moderate, high/);
  assert.match(problems[3], /defined twice/);
});

test("recommendations follow the state they read, rank by reason, and offer ties without picking one", { skip }, async () => {
  const p = await page();
  const now = () => p.evaluate(() => { const r = recommend(); return { ranked: r.ranked, ids: r.items.map((i) => i.id), why: r.items.map((i) => i.why) }; });
  assert.deepStrictEqual(await now(), { ranked: false, ids: [], why: [] });  // nothing found: nothing claimed

  await p.evaluate(() => { t.warnings = 3; });
  assert.deepStrictEqual(await now(), { ranked: true, ids: ["t.rec.warnings"], why: ["3 validation warnings"] });

  await p.evaluate(() => { t.unsaved = true; });
  const both = await now();
  assert.strictEqual(both.ranked, true);  // a validation warning outranks unfinished work
  assert.deepStrictEqual(both.ids, ["t.rec.warnings", "t.rec.save", "t.rec.publish"]);

  await p.evaluate(() => { t.warnings = 0; });
  const tie = await now();
  assert.strictEqual(tie.ranked, false);  // two "unfinished" reasons: equal standing
  assert.deepStrictEqual(tie.ids, ["t.rec.save", "t.rec.publish"]);

  // An action that can't run isn't recommended.
  await p.evaluate(() => { t.signedIn = false; });
  assert.deepStrictEqual((await now()).ids, ["t.rec.save"]);
  await p.evaluate(() => { t.signedIn = true; t.unsaved = false; });
});

test("the next-steps strip says Recommended only when one reason leads, and its button runs the action", { skip }, async () => {
  const p = await page();
  await p.evaluate(() => { t.warnings = 2; t.unsaved = true; document.getElementById("view").prepend(nextSteps()); });
  assert.strictEqual(await p.textContent(".next-steps-head"), "Recommended next");
  assert.match(await p.textContent(".next-step[data-recommendation='t.rec.warnings']"), /Verified\s*2 validation warnings/);
  assert.ok(await p.$(".next-step[data-recommendation='t.rec.warnings'] button.primary"));
  await p.click(".next-step[data-recommendation='t.rec.warnings'] button");
  assert.deepStrictEqual(await p.evaluate(() => t.ran.splice(0)), ["validate"]);

  await p.evaluate(() => { t.warnings = 0; document.querySelector(".next-steps").replaceWith(nextSteps()); });
  assert.strictEqual(await p.textContent(".next-steps-head"), "You could");
  assert.strictEqual(await p.locator(".next-steps button.primary").count(), 0);
  await p.evaluate(() => { t.unsaved = false; document.querySelector(".next-steps").remove(); });
  assert.strictEqual(await p.evaluate(() => nextSteps()), null);
});

test("an action that can't run says why, from the bar, the menu and the command bar", { skip }, async () => {
  const p = await page();
  await p.evaluate(() => { t.signedIn = false; document.getElementById("view").prepend(actionBar()); });
  assert.strictEqual(await p.textContent(".action-bar button.primary"), "Run the test thing");
  await p.click(".action-bar button:has-text('More')");
  const pub = p.locator("#menu [data-action='t.publish']");
  assert.strictEqual(await pub.getAttribute("aria-disabled"), "true");
  assert.strictEqual(await pub.getAttribute("title"), "Sign in to publish to Fabric.");
  assert.strictEqual(await p.locator("#menu [data-action='t.elsewhere']").count(), 0);  // another page's action
  await pub.focus();  // aria-disabled, so still focusable: Enter is how a keyboard user chooses it
  await p.keyboard.press("Enter");
  assert.match(await p.textContent("#toast"), /Publish the test thing: Sign in to publish to Fabric\./);
  assert.deepStrictEqual(await p.evaluate(() => t.ran.splice(0)), []);

  const labels = await p.evaluate(() => paletteItems("test").filter((i) => i.group === "Actions").map((i) => i.label));
  for (const l of ["Run the test thing", "Open the test table", "Publish the test thing", "Delete the test table"]) assert.ok(labels.includes(l), l);
  assert.ok(!(await p.evaluate(() => paletteItems("another page").some((i) => i.label === "Something on another page"))));
  await p.evaluate(() => { t.signedIn = true; document.querySelector(".action-bar").remove(); });
});

test("a high-risk action asks in a danger dialog first; a finished one offers the next step", { skip }, async () => {
  const p = await page();
  const running = p.evaluate(() => runAction("t.drop"));
  await p.waitForSelector(".dialog button.danger-solid:has-text('Delete')");
  assert.match(await p.textContent(".dialog"), /Delete the test table\?[\s\S]*Its rows are gone for good\./);
  await p.click(".dialog button:has-text('Cancel')");
  assert.strictEqual(await running, undefined);
  assert.deepStrictEqual(await p.evaluate(() => t.ran.splice(0)), []);

  await p.evaluate(() => runAction("t.run"));
  assert.match(await p.textContent("#toast"), /Wrote 3 rows/);
  await p.click("#toast button:has-text('Open the test table')");
  assert.deepStrictEqual(await p.evaluate(() => t.ran.splice(0)), ["run", "open"]);
  assert.deepStrictEqual(app.errors, []);
});

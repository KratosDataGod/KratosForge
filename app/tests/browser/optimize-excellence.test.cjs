// FEA-109: Optimize > Assessment > Excellence, driven by the real engine's demonstration fixture
// (excellence_assess(demo=True) makes no Fabric call). See harness.cjs for how to run it.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

// axe covers dialogs (#1673, ui.test.cjs): the dimension and finding cases below run axe while each is open.
// axe covers dialogs: optimize-excellence.js:excDimension, optimize-excellence.js:excFinding

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

let app;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["excellence_assess", "optimization_assess", "app_settings", "save_app_settings"],
    mocks: { optimize_status: () => ({ allowed: true, active: null, connections: [], default_name: "FabricDesktop_Central" }) },
  });
  await app.page.waitForFunction(() => typeof optimizeExcellenceTabs === "function" && !!state.settings);
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const page = () => app.page;
const shot = async (name) => { if (SHOTS) await page().screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true }); };

test("Assessment opens on Excellence, with Opportunities and Gaps beside it, and reads nothing until asked", { skip }, async () => {
  await page().evaluate(() => { optimizeExcellence.view = "excellence"; optimizeExcellence.result = null; optimizePage.tab = "assessment"; go("optimize"); });
  await page().waitForSelector(".exc-empty");
  const views = await page().locator(".exc-views .tab").allTextContents();
  assert.deepStrictEqual(views, ["Excellence", "Opportunities", "Gaps"]);
  const text = await page().locator(".page.optimize").textContent();
  assert.match(text, /Reads only, with your own access\. Nothing is changed in Fabric/);
  assert.match(text, /not a Microsoft certification/);
  assert.match(text, /counts against coverage, never as passed/);
  assert.ok(!app.calls.some((c) => c.method === "excellence_assess"), "nothing is assessed until asked");
});

test("the demonstration scorecard shows score, assured score, coverage and ten dimensions, all labelled", { skip }, async () => {
  await page().locator(".exc-empty").getByRole("button", { name: "Try the demonstration fixture" }).click();
  await page().waitForSelector(".exc-hero");
  const call = app.calls.find((c) => c.method === "excellence_assess");
  assert.deepStrictEqual(call.params, { demo: true });
  const text = await page().locator(".exc-view").textContent();
  assert.match(text, /DEMONSTRATION FIXTURE\. [A-Z]/);
  for (const part of ["Overall score", "Assured score", "Coverage", "Status", "Ten dimensions", "Next actions", "Passed", "Not evaluated"]) {
    assert.ok(text.includes(part), part);
  }
  assert.match(text, /Profile: Baseline \(default\), version \d/);
  assert.match(text, /KratosForge product rating, not a certification/);
  assert.doesNotMatch(text, /Microsoft-aligned|MVP/);
  assert.equal(await page().locator(".exc-dim").count(), 10);
  // Dimensions no control covers yet are shown as not applicable, never as passing.
  const na = await page().locator(".exc-dim.ne").allTextContents();
  assert.ok(na.length >= 1 && na.every((t) => /Not applicable|Not evaluated/.test(t)), na.join(" | "));
  // Next actions: severity first, never more than five.
  const sev = await page().locator(".exc-act .exc-sev").allTextContents();
  const rank = { Critical: 0, High: 1, Medium: 2, Low: 3, Info: 4 };
  assert.ok(sev.length >= 1 && sev.length <= 5, sev.join(","));
  assert.deepStrictEqual([...sev].sort((a, b) => rank[a] - rank[b]), sev);
  const scope = await page().locator(".exc-scope").textContent();
  assert.match(scope, /Leave the demonstration/);
  assert.doesNotMatch(scope, /null|undefined/);
  assert.deepStrictEqual(app.errors, []);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".page.optimize"), []);
  await page().locator(".exc-acts").scrollIntoViewIfNeeded();
  await shot("excellence-demo");
});

test("a dimension opens its controls, outcomes and the evidence each cites", { skip }, async () => {
  await page().locator(".exc-dim", { hasText: "Security" }).click();
  const sheet = page().locator(".dialog.exc-sheet");
  await sheet.waitFor();
  const text = await sheet.textContent();
  assert.match(text, /FEA-SEC-00\d/);
  assert.match(text, /Passed/);
  assert.match(text, /Failed/);
  assert.match(text, /Weight 18 of 100/);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), []);
  await page().keyboard.press("Escape");
  await sheet.waitFor({ state: "detached" });
});

test("a next action opens the full finding: why, steps, validation, rollback and the rule version", { skip }, async () => {
  await page().locator(".exc-act").first().getByRole("button", { name: /^Open the finding/ }).click();
  const sheet = page().locator(".dialog.assess-sheet");
  await sheet.waitFor();
  const text = await sheet.textContent();
  for (const part of ["What was found", "Why it matters", "Recommended", "How to check it worked", "How to undo it", "Evidence", "References", "Demonstration fixture"]) {
    assert.ok(text.includes(part), part);
  }
  assert.match(text, /Rule FEA-[A-Z]+-\d{3}@\d+\.\d+\.\d+/);
  assert.match(text, /Verified configuration/);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), []);
  await page().waitForTimeout(400); // the dialog's fade-in, for the screenshot
  await shot("excellence-finding");
  await page().keyboard.press("Escape");
  await sheet.waitFor({ state: "detached" });
});

test("Gaps lists the items no control covers yet, and Opportunities is the existing view", { skip }, async () => {
  await page().locator(".exc-views .tab", { hasText: "Gaps" }).click();
  const gaps = await page().locator(".exc-inner").textContent();
  assert.match(gaps, /DEMONSTRATION FIXTURE/);
  assert.match(gaps, /Items not inspected \(\d+\)/);
  assert.match(gaps, /not supported yet\. No control covers this kind of item yet\./);
  await page().locator(".exc-views .tab", { hasText: "Opportunities" }).click();
  await page().waitForSelector(".assess-empty, .assess-table");
  assert.ok((await page().locator(".assess-scope").count()) === 1);
  assert.deepStrictEqual(app.errors, []);
});

test("provisional, insufficient evidence and Critical Risk read as the mockup's other states", { skip }, async () => {
  const draw = (mutate) => page().evaluate((src) => {
    const r = JSON.parse(JSON.stringify(window.__excBase || (window.__excBase = optimizeExcellence.result)));
    new Function("r", src)(r);
    optimizeExcellence.result = r; optimizeExcellence.view = "excellence"; render();
  }, mutate);
  await draw(`Object.assign(r.scores.overall, { status: "provisional", coverage: 68 });`);
  await page().waitForSelector(".exc-hero");
  let text = await page().locator(".exc-view").textContent();
  assert.match(text, /Provisional/);
  assert.match(text, /The rating is provisional because only 68% could be checked/);
  await draw(`Object.assign(r.scores.overall, { status: "insufficient_evidence", coverage: 34, rating: null });`);
  await page().waitForSelector(".exc-info.grey");
  text = await page().locator(".exc-hero").textContent();
  assert.match(text, /Overall score—Insufficient evidence/);
  await draw(`r.scores.critical_risk = true; r.findings[0].severity = "critical";`);
  await page().waitForSelector(".exc-crit");
  text = await page().locator(".exc-crit").textContent();
  assert.match(text, /Critical Risk\. A critical control failed: .+ This flag stands whatever the score\./);
  assert.deepStrictEqual(app.errors, []);
  await page().evaluate(() => { optimizeExcellence.result = window.__excBase; });
});

test("a run that ends after you switch views still fills the Excellence panel you come back to", { skip }, async () => {
  await page().evaluate(() => { optimizeExcellence.result = null; optimizeExcellence.view = "excellence"; render(); });
  await page().waitForSelector(".exc-empty");
  // All in one tick, so the engine can't answer before the views switch.
  const stuck = await page().evaluate(() => {
    const tab = (name) => [...document.querySelectorAll(".exc-views .tab")].find((t) => t.textContent === name);
    [...document.querySelectorAll(".exc-empty button")].find((b) => b.textContent.includes("demonstration")).click();
    tab("Gaps").click();
    tab("Excellence").click();
    return !!document.querySelector(".exc-view [role=status]");
  });
  assert.ok(stuck, "the new panel shows the run in progress");
  await page().waitForSelector(".exc-hero");
  assert.equal(await page().locator(".exc-scope .tool.primary").isDisabled(), false);
});

test("the scorecard works from the keyboard and fits a narrow window", { skip }, async () => {
  await page().locator(".exc-dim").first().focus();
  await page().keyboard.press("Enter");
  await page().locator(".dialog.exc-sheet").waitFor();
  await page().keyboard.press("Escape");
  await page().locator(".dialog.exc-sheet").waitFor({ state: "detached" });
  const size = page().viewportSize();
  await page().setViewportSize({ width: 420, height: 900 });
  try {
    const over = await page().evaluate(() => [...document.querySelectorAll(".exc-hero, .exc-dims, .exc-acts, .exc-counts")]
      .filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.className));
    assert.deepStrictEqual(over, []);
    await shot("excellence-narrow");
  } finally {
    await page().setViewportSize(size);
  }
});

test("an excellence score is never rounded up to 100", { skip }, async () => {
  const shown = await page().evaluate(() => [excNum(99.6), excNum(100), excNum(84.4), excNum(null)]);
  assert.deepStrictEqual(shown, ["99", "100", "84", "—"]);
  assert.equal(await page().evaluate(() => excNeeds("workspace.roles, the users' sign-in names")), "role assignments, the users' sign-in names");
});

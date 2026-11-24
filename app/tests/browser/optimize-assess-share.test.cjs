// OE-004: Share on Optimize > Assessment's Opportunities header (optimize-assess.js assessShare), from the
// UX mockup. The assessment is the real engine's demonstration fixture, shown as if it were a real one
// (a share_id, fixture off); the central lakehouse and the share calls are mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

// axe covers dialogs: optimize-assess.js:assessShare

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const LH = "lh_optimization";
const TEXT = `Adds 6 findings and 6 recommendations from this assessment to ${LH}, about 11 items in 1 workspace: their names and ids, `
  + "what was found and why, and the evidence and impact figures, with anything that looks like a secret removed. No code or data, "
  + "and everyone who can read the lakehouse sees them, whatever they can open in Fabric.";
const WOULD = { findings: 6, recommendations: 6, items: 11, workspaces: 1, shared: 0, confirm: "c0ffee", lakehouse: LH, connected: true, access: "write", text: TEXT };
const connected = { allowed: true, active: { lakehouse_id: "l1", workspace_id: "w1", lakehouse_name: LH, access: "write" }, connections: [] };

let app;
let status = connected;
let share = () => ({ ...WOULD, shared: 6, queued: 0 });
let preview = () => WOULD;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["optimization_assess", "app_settings", "save_app_settings"],
    mocks: {
      optimize_status: () => status,
      optimization_share_preview: (p) => preview(p),
      optimization_share: (p) => share(p),
    },
  });
  await app.page.waitForFunction(() => typeof optimizeAssessPanel === "function" && !!state.settings);
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const page = () => app.page;
const shot = async (name) => { if (SHOTS) await page().screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true }); };
const shareBtn = () => page().locator(".assess-share-acts button");
const line = () => page().locator(".assess-share-line");
// Draw the Assessment tab again with the fixture's result shown as a real, shareable one.
const real = async (shareState = null) => {
  await page().evaluate((s) => {
    optimizeAssess.result = { ...optimizeAssess.result, fixture: false, fixture_note: "", share_id: "oe-1" };
    optimizeAssess.share = s;
    optimizeExcellence.view = "opportunities"; optimizePage.tab = "assessment"; go("optimize");
  }, shareState);
  await page().waitForSelector(".assess-table");
  await page().waitForFunction(() => !!document.querySelector(".assess-share-acts button"));
};
const sharesSent = () => app.calls.filter((c) => c.method === "optimization_share");

test("the demonstration fixture has no Share", { skip }, async () => {
  await page().evaluate(() => { optimizeExcellence.view = "opportunities"; optimizePage.tab = "assessment"; go("optimize"); });
  await page().getByRole("button", { name: "See how it works with the demonstration fixture" }).click();
  await page().waitForSelector(".assess-table");
  assert.equal(await shareBtn().count(), 0);
  assert.ok(!app.calls.some((c) => c.method.startsWith("optimization_share")));
});

test("Share asks first: the dialog shows the engine's own words and sends back its confirm", { skip }, async () => {
  await real();
  await page().waitForFunction(() => !document.querySelector(".assess-share-acts button").disabled);
  assert.equal(await shareBtn().textContent(), "Share with your team...");
  // Share sits on the Opportunities header, not with Assess, whose row says it only reads.
  assert.equal(await page().locator(".assess-scope .assess-share-acts").count(), 0);
  await shareBtn().click();
  const dlg = page().locator(".dialog");
  await dlg.waitFor();
  const text = await dlg.textContent();
  assert.match(text, new RegExp(`Share 6 findings to ${LH}\\?`));
  assert.ok(text.includes(TEXT), "the engine's sentence, word for word");
  assert.doesNotMatch(text, /Confirm to continue/);
  for (const k of ["Findings", "Recommendations", "Items", "Workspaces"]) assert.ok(text.includes(k), k);
  assert.equal(sharesSent().length, 0, "asking writes nothing");
  assert.equal(await dlg.getByRole("button", { name: "Share 6 findings" }).getAttribute("class"), "tool primary");
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), []);
  await page().waitForTimeout(400);
  await shot("assess-share-confirm");
  await dlg.getByRole("button", { name: "Share 6 findings" }).click();
  await page().waitForSelector(".assess-share-state.ok");
  assert.deepStrictEqual(sharesSent().at(-1).params, { share_id: "oe-1", approve: true, confirm: "c0ffee" });
  assert.match(await line().textContent(), new RegExp(`Shared to ${LH} at .+\\. 6 findings and 6 recommendations\\.`));
  assert.equal(await shareBtn().textContent(), "Shared");
  assert.ok(await shareBtn().isDisabled());
  assert.deepStrictEqual(app.errors, []);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".page.optimize"), []);
  await shot("assess-share-shared");
});

test("Cancel writes nothing", { skip }, async () => {
  await real();
  await page().waitForFunction(() => !document.querySelector(".assess-share-acts button").disabled);
  const before = sharesSent().length;
  await shareBtn().click();
  await page().locator(".dialog").getByRole("button", { name: "Cancel" }).click();
  await page().locator(".dialog").waitFor({ state: "detached" });
  assert.equal(sharesSent().length, before);
  assert.equal(await line().textContent(), "");
});

test("if the findings changed, the engine asks again and the dialog says so", { skip }, async () => {
  await real();
  await page().waitForFunction(() => !document.querySelector(".assess-share-acts button").disabled);
  let first = true;
  share = () => {
    if (first) { first = false; throw new Error(`PermissionError: What would be added changed since you looked. ${TEXT}`); }
    return { ...WOULD, shared: 6, queued: 0 };
  };
  preview = () => ({ ...WOULD, confirm: first ? "c0ffee" : "beef" });
  await shareBtn().click();
  await page().locator(".dialog").getByRole("button", { name: "Share 6 findings" }).click();
  await page().waitForSelector(".dialog .assess-share-state.warn");
  assert.match(await page().locator(".dialog").textContent(), /The findings changed since you looked/);
  await page().locator(".dialog").getByRole("button", { name: "Share 6 findings" }).click();
  await page().waitForSelector(".assess-share-state.ok");
  assert.equal(sharesSent().at(-1).params.confirm, "beef", "the new confirm, not the old one");
  share = () => ({ ...WOULD, shared: 6, queued: 0 });
  preview = () => WOULD;
});

test("waiting, refused and after a restart each say what happened", { skip }, async () => {
  share = () => ({ ...WOULD, shared: 0, queued: 6, error: "timed out" });
  await real();
  await page().waitForFunction(() => !document.querySelector(".assess-share-acts button").disabled);
  await shareBtn().click();
  await page().locator(".dialog").getByRole("button", { name: "Share 6 findings" }).click();
  await page().waitForSelector(".assess-share-state.wait");
  assert.match(await line().textContent(), /Waiting to share\..*the 6 findings wait on this PC/);
  assert.equal(await shareBtn().textContent(), "Waiting to share");
  await shot("assess-share-waiting");

  share = () => { throw new Error("You need Contributor on its workspace."); };
  await real();
  await page().waitForFunction(() => !document.querySelector(".assess-share-acts button").disabled);
  await shareBtn().click();
  await page().locator(".dialog").getByRole("button", { name: "Share 6 findings" }).click();
  await page().waitForSelector(".assess-share-state.warn[role=alert]");
  assert.match(await line().textContent(), /Couldn't share\..*Contributor.*Nothing was shared\./);
  assert.ok(await line().getByRole("button", { name: "Try again" }).isVisible());

  preview = () => { throw new Error("ValueError: That assessment isn't kept on this PC any more (KratosForge restarted, or 5 newer ones were made). Assess again, then share it."); };
  await line().getByRole("button", { name: "Try again" }).click();
  await page().waitForFunction(() => /can't be shared anymore/.test(document.querySelector(".assess-share-line").textContent));
  assert.ok(await shareBtn().isDisabled());
  await shot("assess-share-restart");
  share = () => ({ ...WOULD, shared: 6, queued: 0 });
  preview = () => WOULD;
});

test("with no central lakehouse, Share is off and the line beside it says where to set one up", { skip }, async () => {
  status = { allowed: true, active: null, connections: [] };
  await real();
  await page().waitForSelector("#assess-share-why");
  assert.ok(await shareBtn().isDisabled());
  assert.equal(await shareBtn().getAttribute("aria-describedby"), "assess-share-why");
  assert.match(await line().textContent(), /Sharing needs a central lakehouse\..*Settings > Optimize lakehouses/);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".page.optimize"), []);
  await shot("assess-share-no-lakehouse");
  status = { ...connected, active: { ...connected.active, access: "read" } };
  await real();
  await page().waitForSelector("#assess-share-why");
  assert.ok(await shareBtn().isDisabled());
  assert.match(await line().textContent(), /You can read but not write to lh_optimization\./);
  status = connected;
  assert.deepStrictEqual(app.errors, []);
});
